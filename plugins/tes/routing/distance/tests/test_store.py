"""Tests of the plugin store entries of `tes.routing.distance` (`store.py`)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from unittest.mock import MagicMock

from foca.plugins import Location, Principal, Store, StoreError
from foca.plugins.store import MemoryStoreBackend, StoreSettings
import pytest

from foca_plugins.tes.routing.distance.store import StoreCache, UserBudget

LOG = logging.LoggerAdapter(logging.getLogger("foca.plugins.tes.routing.distance"))


def make_store(
    backend: Any = None, clock: Callable[[], datetime] | None = None
) -> Store:
    """Create a plugin store, as `ctx.store`.

    Args:
        backend: Backend; one in memory if not given.
        clock: Clock of the store; the real one if not given.

    Returns:
        Store, without deadline.
    """
    settings = StoreSettings(
        namespace="foca-plugin-tes-routing-distance",
        mode="local",
        backend=MemoryStoreBackend() if backend is None else backend,
        max_entry_size=4096,
        max_ttl=2_592_000,
    )
    return Store(settings) if clock is None else Store(settings, clock=clock)


def failing_backend() -> MagicMock:
    """Create a store backend whose calls fail.

    Returns:
        Backend.
    """
    error = StoreError("Plugin store failed: ServerSelectionTimeoutError")
    return MagicMock(
        **{
            f"{name}.side_effect": error
            for name in ("get", "set", "delete", "increment")
        }
    )


def test_store_cache_hits_and_expires() -> None:
    """Locations and misses are kept in the store, for their TTL."""
    now = [datetime(2026, 11, 13, tzinfo=UTC)]
    store = make_store(clock=lambda: now[0])
    cache = StoreCache(store, LOG)
    here = Location(latitude=1, longitude=2)
    assert cache.get("9.9.9.9") == (False, None)
    cache.put("9.9.9.9", here, 100)
    cache.put("4.4.4.4", None, 10)
    assert cache.get("9.9.9.9") == (True, here)
    assert cache.get("4.4.4.4") == (True, None)
    # another call (or process) with the same store finds them
    assert StoreCache(store, LOG).get("9.9.9.9") == (True, here)
    now[0] += timedelta(seconds=11)
    assert cache.get("4.4.4.4") == (False, None)
    assert cache.get("9.9.9.9") == (True, here)
    now[0] += timedelta(seconds=90)
    assert cache.get("9.9.9.9") == (False, None)


def test_store_cache_keys_are_hashes() -> None:
    """Addresses aren't kept as keys, only hashes derived by the store."""
    store = make_store()
    StoreCache(store, LOG).put("9.9.9.9", Location(latitude=1, longitude=2), 100)
    assert store.get("9.9.9.9") is None
    assert store.get(store.key_for("location", "9.9.9.9")) == [1, 2]


@pytest.mark.parametrize(("enabled", "ttl"), [(False, 100), (True, 0)])
def test_store_cache_can_be_turned_off(enabled: bool, ttl: int) -> None:
    """A turned-off cache keeps nothing, nor does TTL 0.

    Args:
        enabled: Whether the cache is on.
        ttl: TTL of the value.
    """
    store = make_store()
    cache = StoreCache(store, LOG, enabled=enabled)
    cache.put("9.9.9.9", Location(latitude=1, longitude=1), ttl)
    cache.add("4.4.4.4", None, ttl)
    assert cache.get("9.9.9.9") == (False, None)
    assert store.get(store.key_for("location", "9.9.9.9")) is None
    assert store.get(store.key_for("location", "4.4.4.4")) is None


def test_store_cache_add_keeps_unexpired_entries() -> None:
    """`add()` stores only addresses that aren't cached."""
    store = make_store()
    cache = StoreCache(store, LOG)
    here = Location(latitude=1, longitude=2)
    cache.put("9.9.9.9", here, 100)
    cache.add("9.9.9.9", None, 10)
    assert cache.get("9.9.9.9") == (True, here)
    cache.add("1.1.1.1", None, 10)
    assert cache.get("1.1.1.1") == (True, None)


@pytest.mark.parametrize("value", [[91, 0], [1], ["x", "y"], {"lat": 1}, 3, None])
def test_store_cache_ignores_invalid_entries(value: Any) -> None:
    """Entries that are neither locations nor misses count as not cached.

    Args:
        value: Stored value.
    """
    store = make_store()
    store.set(store.key_for("location", "9.9.9.9"), value, ttl=100)
    assert StoreCache(store, LOG).get("9.9.9.9") == (False, None)


def test_store_cache_survives_store_failures(caplog: pytest.LogCaptureFixture) -> None:
    """A failing store leaves addresses uncached; the call asks it no more.

    Args:
        caplog: Pytest fixture.
    """
    backend = failing_backend()
    cache = StoreCache(make_store(backend), LOG)
    with caplog.at_level("WARNING"):
        assert cache.get("9.9.9.9") == (False, None)
        cache.put("9.9.9.9", Location(latitude=1, longitude=1), 100)
        cache.add("4.4.4.4", None, 100)
        assert cache.get("9.9.9.9") == (False, None)
    assert backend.get.call_count == 1
    assert backend.set.call_count == 0
    (record,) = caplog.records
    assert "ServerSelectionTimeoutError" in record.getMessage()
    assert "9.9.9.9" not in record.getMessage()


def test_store_cache_survives_failing_writes() -> None:
    """A failing write leaves the address uncached."""
    backend = MagicMock(wraps=MemoryStoreBackend())
    backend.set.side_effect = StoreError("Plugin store failed: WriteError")
    cache = StoreCache(make_store(backend), LOG)
    cache.put("9.9.9.9", Location(latitude=1, longitude=1), 100)
    assert cache.get("9.9.9.9") == (False, None)
    assert backend.get.call_count == 0


def test_budget_counts_per_caller() -> None:
    """Each caller gets `limit` lookups per window; anonymous ones share one."""
    store = make_store()
    alice = UserBudget(store, Principal(subject="alice"), 2, LOG)
    assert [alice.take(), alice.take(), alice.take()] == [True, True, False]
    # a new call of the same caller: the budget is used up
    assert not UserBudget(store, Principal(subject="alice"), 2, LOG).take()
    # a service client with the same identifier is another caller
    client = Principal(subject="alice", subject_type="client")
    assert UserBudget(store, client, 2, LOG).take()
    anonymous = [UserBudget(store, Principal(), 2, LOG) for _ in range(3)]
    assert [budget.take() for budget in anonymous] == [True, True, False]


def test_budget_keys_are_hashes() -> None:
    """The counter's key is a hash, not the caller's identifier."""
    store = make_store()
    UserBudget(store, Principal(subject="alice"), 2, LOG).take()
    key = store.key_for("dbip-lookups", "user", "alice")
    assert key.startswith("h:")
    assert store.get(key) == 1


def test_budget_is_a_fixed_window() -> None:
    """Counts reset a day after the first lookup of the window."""
    now = [datetime(2026, 11, 13, tzinfo=UTC)]
    store = make_store(clock=lambda: now[0])
    assert UserBudget(store, Principal(subject="alice"), 1, LOG).take()
    now[0] += timedelta(hours=23)
    assert not UserBudget(store, Principal(subject="alice"), 1, LOG).take()
    now[0] += timedelta(hours=1)
    assert UserBudget(store, Principal(subject="alice"), 1, LOG).take()


def test_budget_gives_back_unused_lookups() -> None:
    """A lookup given back can be taken again."""
    store = make_store()
    budget = UserBudget(store, Principal(subject="alice"), 1, LOG)
    assert budget.take()
    budget.give_back()
    assert budget.take()
    assert not budget.take()


def test_budget_gives_back_nothing_to_expired_counters() -> None:
    """Giving back to an expired counter starts no window below 0."""
    now = [datetime(2026, 11, 13, tzinfo=UTC)]
    store = make_store(clock=lambda: now[0])
    budget = UserBudget(store, Principal(subject="alice"), 1, LOG)
    assert budget.take()
    now[0] += timedelta(days=1)
    budget.give_back()
    assert store.get(budget.key) is None
    assert budget.take()
    assert not budget.take()


def test_budget_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    """Without a working store, no lookup is allowed; it is asked once.

    Args:
        caplog: Pytest fixture.
    """
    backend = failing_backend()
    budget = UserBudget(make_store(backend), Principal(subject="alice"), 5, LOG)
    with caplog.at_level("WARNING"):
        assert not budget.take()
        assert not budget.take()
        budget.give_back()
    assert backend.increment.call_count == 1
    (record,) = caplog.records
    assert "ServerSelectionTimeoutError" in record.getMessage()
    assert "alice" not in record.getMessage()


def test_budget_survives_failing_give_back() -> None:
    """A failed give-back leaves the lookup counted."""
    backend = MagicMock(wraps=MemoryStoreBackend())
    store = make_store(backend)
    budget = UserBudget(store, Principal(subject="alice"), 5, LOG)
    assert budget.take()
    backend.increment.side_effect = StoreError("Plugin store failed: WriteError")
    budget.give_back()
    assert store.get(store.key_for("dbip-lookups", "user", "alice")) == 1
