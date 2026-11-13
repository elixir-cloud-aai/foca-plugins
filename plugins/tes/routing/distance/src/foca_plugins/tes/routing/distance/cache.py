"""Caches of the plugin's lookups.

- :py:class:`TtlCache`: in the memory of the process, with TTLs and a size
  limit (for host names, places and, with the offline provider, the
  locations of addresses).
- :py:class:`LocationCache`: what the plugin needs of a cache of
  locations; :py:class:`TtlCache` and
  :py:class:`~foca_plugins.tes.routing.distance.store.StoreCache` (in the
  plugin store, for the online provider) have it.
"""

from collections import OrderedDict
from collections.abc import Callable, Hashable
import threading
import time
from typing import Generic, Protocol, TypeVar

from foca.plugins import Location

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LocationCache(Protocol):
    """Cache of the locations of addresses; `None` for misses."""

    def get(self, key: str) -> tuple[bool, Location | None]:
        """Get the cached location of an address.

        Args:
            key: Address.

        Returns:
            Whether the address is cached, and its location (`None` if not
            cached or a miss).
        """
        ...  # pragma: no cover

    def put(self, key: str, value: Location | None, ttl: float) -> None:
        """Cache the location of an address.

        Args:
            key: Address.
            value: Location; `None` for a miss.
            ttl: Seconds to keep it; 0 keeps it not at all.
        """
        ...  # pragma: no cover

    def add(self, key: str, value: Location | None, ttl: float) -> None:
        """Cache the location of an address unless it is cached already.

        Args:
            key: Address.
            value: Location; `None` for a miss.
            ttl: Seconds to keep it; 0 keeps it not at all.
        """
        ...  # pragma: no cover


class TtlCache(Generic[K, V]):
    """Cache whose entries expire, each after a TTL of its own.

    Beyond its size, the entry used least recently goes. Safe for
    concurrent use.

    Attributes:
        size: Maximum number of entries; 0 turns the cache off.
    """

    def __init__(self, size: int, clock: Callable[[], float] = time.monotonic) -> None:
        """Create the cache.

        Args:
            size: Maximum number of entries; 0 turns the cache off.
            clock: Clock, for tests.
        """
        self.size = size
        self._clock = clock
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K) -> tuple[bool, V | None]:
        """Get a cached value.

        Args:
            key: Key.

        Returns:
            Whether the key is cached (and not expired), and its value
            (`None` if not cached).
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False, None
            expires, value = entry
            if expires <= self._clock():
                del self._entries[key]
                return False, None
            self._entries.move_to_end(key)
            return True, value

    def put(self, key: K, value: V, ttl: float) -> None:
        """Cache a value.

        Args:
            key: Key.
            value: Value.
            ttl: Seconds to keep the value; 0 keeps it not at all.
        """
        if self.size <= 0 or ttl <= 0:
            return
        with self._lock:
            self._entries[key] = (self._clock() + ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)

    def add(self, key: K, value: V, ttl: float) -> None:
        """Cache a value unless the key is cached already (and not expired).

        Args:
            key: Key.
            value: Value.
            ttl: Seconds to keep the value; 0 keeps it not at all.
        """
        if self.size <= 0 or ttl <= 0:
            return
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > self._clock():
                return
            self._entries[key] = (self._clock() + ttl, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)
