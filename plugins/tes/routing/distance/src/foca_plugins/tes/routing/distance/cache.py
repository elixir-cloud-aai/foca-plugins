"""Cache with TTLs and a size limit, for lookups of the plugin."""

from collections import OrderedDict
from collections.abc import Callable, Hashable
import threading
import time
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


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
