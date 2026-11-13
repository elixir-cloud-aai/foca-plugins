"""Entries of the plugin in FOCA's plugin store (`ctx.store`).

With the online provider (`dbip`), the plugin keeps in the store:

- the locations of addresses, also misses (:py:class:`StoreCache`), so
  that all processes and replicas that share the store look up each
  address once until its entry expires;
- a daily count of the lookups of each caller (:py:class:`UserBudget`),
  so that no single user can use up DB-IP's daily quota.

Keys are derived with `ctx.store.key_for()`: hashes (HMACs, if the
operator set FOCA's key secret) of the address or of the caller's
identity, never the address or identifier as they are. Store failures
never fail a call: the cache then counts addresses as not cached, the
budget allows no lookup (it fails closed), and neither asks the store
again in that call.
"""

import logging
import math
from typing import Any

from foca.plugins import Location, Principal, Store, StoreError

LOCATION_KEY = "location"
"""First part of the keys of locations (cf. `Store.key_for()`)."""

BUDGET_KEY = "dbip-lookups"
"""First part of the keys of the callers' lookup counts."""

BUDGET_WINDOW = 86400
"""Seconds of the window of the callers' lookup counts (a day)."""


def _location(value: Any) -> tuple[bool, Location | None]:
    """Read a stored location.

    Args:
        value: Stored value: `[latitude, longitude]`, or `[]` for a miss.

    Returns:
        Whether the value is a valid location or miss, and the location
        (`None` for a miss or an invalid value).
    """
    if value == []:
        return True, None
    if not (isinstance(value, list) and len(value) == 2):
        return False, None
    latitude, longitude = value
    for number in value:
        if (
            isinstance(number, bool)
            or not isinstance(number, int | float)
            or not math.isfinite(number)
        ):
            return False, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return False, None
    return True, Location(latitude=latitude, longitude=longitude)


class StoreCache:
    """Cache of the locations of addresses in the plugin store, for one call.

    Has the methods of :py:class:`.cache.LocationCache`. Entries are kept
    for their TTL, capped by the operator's `max_ttl` of the store; the
    cache has no size limit of its own, but the store may be full (FOCA
    0.23's `max_namespace_size`), which counts as a failure. Entries that
    are neither locations nor misses count as not cached. After the first
    failure of the store, the cache counts all addresses as not cached and
    keeps none, for the rest of the call.

    Attributes:
        store: Plugin store of the call.
        log: Logger of the call.
        enabled: Whether the cache keeps entries.
        failed: Whether the store failed in this call.
    """

    def __init__(
        self,
        store: Store,
        log: "logging.LoggerAdapter[logging.Logger]",
        *,
        enabled: bool = True,
    ) -> None:
        """Create the cache of a call.

        Args:
            store: Plugin store of the call (`ctx.store`).
            log: Logger of the call (`ctx.log`).
            enabled: Whether the cache keeps entries; if not, it counts all
                addresses as not cached.
        """
        self.store = store
        self.log = log
        self.enabled = enabled
        self.failed = False

    def _failure(self, exc: StoreError) -> None:
        """Note a failure of the store; logs it, without the address.

        Args:
            exc: Error of the store.
        """
        self.failed = True
        self.log.warning("Plugin store unavailable for locations: %s", exc)

    def get(self, key: str) -> tuple[bool, Location | None]:
        """Get the cached location of an address.

        Args:
            key: Address.

        Returns:
            Whether the address is cached, and its location (`None` if not
            cached or a miss).
        """
        if not self.enabled or self.failed:
            return False, None
        try:
            value = self.store.get(self.store.key_for(LOCATION_KEY, key))
        except StoreError as exc:
            self._failure(exc)
            return False, None
        return _location(value)

    def put(self, key: str, value: Location | None, ttl: float) -> None:
        """Cache the location of an address.

        Args:
            key: Address.
            value: Location; `None` for a miss.
            ttl: Seconds to keep it; 0 keeps it not at all.
        """
        if not self.enabled or self.failed or ttl <= 0:
            return
        stored = [] if value is None else [value.latitude, value.longitude]
        try:
            self.store.set(self.store.key_for(LOCATION_KEY, key), stored, ttl=ttl)
        except StoreError as exc:
            self._failure(exc)

    def add(self, key: str, value: Location | None, ttl: float) -> None:
        """Cache the location of an address unless it is cached already.

        Not atomic: a location that another process stores between the
        check and the write is replaced.

        Args:
            key: Address.
            value: Location; `None` for a miss.
            ttl: Seconds to keep it; 0 keeps it not at all.
        """
        if not self.enabled or ttl <= 0:
            return
        cached, _ = self.get(key)
        if not cached:
            self.put(key, value, ttl)


class UserBudget:
    """Daily budget of lookups of the caller of one call, in the plugin store.

    A counter per caller (`subject_type` and `subject` of the principal;
    all anonymous callers share one), which starts with the caller's first
    lookup and expires a day later (a fixed window; capped by the
    operator's `max_ttl` of the store). A service client has one budget,
    whichever users it acts for. If the store fails, the budget allows no
    lookup, and asks the store no more, for the rest of the call.

    Attributes:
        store: Plugin store of the call.
        key: Key of the caller's counter.
        limit: Maximum number of lookups per window.
        log: Logger of the call.
        exhausted: Whether the caller's budget is used up.
        failed: Whether the store failed in this call.
    """

    def __init__(
        self,
        store: Store,
        principal: Principal,
        limit: int,
        log: "logging.LoggerAdapter[logging.Logger]",
    ) -> None:
        """Create the budget of a call.

        Args:
            store: Plugin store of the call (`ctx.store`).
            principal: Caller (`ctx.principal`).
            limit: Maximum number of lookups per caller and window.
            log: Logger of the call (`ctx.log`).
        """
        self.store = store
        self.key = store.key_for(
            BUDGET_KEY, principal.subject_type or "", principal.subject or ""
        )
        self.limit = limit
        self.log = log
        self.exhausted = False
        self.failed = False

    def take(self) -> bool:
        """Count a lookup, if the budget allows it.

        Returns:
            Whether the lookup may run.
        """
        if self.exhausted or self.failed:
            return False
        try:
            count = self.store.increment(self.key, ttl=BUDGET_WINDOW, limit=self.limit)
        except StoreError as exc:
            self.failed = True
            self.log.warning("Plugin store unavailable for the budget: %s", exc)
            return False
        if count is None:
            self.exhausted = True
            self.log.info("The caller's daily budget of lookups is used up")
            return False
        return True

    def give_back(self) -> None:
        """Uncount a lookup that sent no request; failures leave it counted.

        Counters that expired (or were never positive) are left alone, so
        that no new window starts below 0 (not atomic: a counter that
        expires between the check and the decrement still does, rarely).
        """
        if self.failed:
            return
        try:
            count = self.store.get(self.key)
            if not isinstance(count, int) or count <= 0:
                return
            self.store.increment(self.key, -1, ttl=BUDGET_WINDOW)
        except StoreError as exc:
            self.failed = True
            self.log.warning("Plugin store unavailable for the budget: %s", exc)
