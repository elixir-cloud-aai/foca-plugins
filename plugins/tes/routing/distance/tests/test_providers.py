"""Tests of the location providers and the cache of `tes.routing.distance`."""

from pathlib import Path
import threading
import time
from typing import Any
from unittest.mock import MagicMock

from foca.plugins import Context, Location, PluginError, Principal
from foca.plugins.base import EmptyModel
from foca.plugins.testing import FakeHttp
from foca.utils.http import RequestMaybeSent
import pytest
import requests

from foca_plugins.tes.routing.distance.cache import TtlCache
from foca_plugins.tes.routing.distance.providers import (
    DbipProvider,
    LookupFailed,
    OfflineProvider,
    coordinates,
)

DBIP = "https://api.db-ip.com/v2/free"
NOMINATIM = "https://nominatim.openstreetmap.org"
AGENT = "test-agent/1.0"
ADDRESS = "9.9.9.9"
PLACE = {"city": "Zurich", "stateProv": "Zurich", "countryCode": "CH"}


class Clock:
    """Clock that tests move by hand, with a `sleep` that moves it.

    Attributes:
        now: Current time, in seconds.
        slept: Durations passed to `sleep`, in order.
    """

    def __init__(self) -> None:
        """Start at 1000 s."""
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        """Get the time.

        Returns:
            Current time.
        """
        return self.now

    def sleep(self, seconds: float) -> None:
        """Move the clock forward.

        Args:
            seconds: Duration.
        """
        self.slept.append(seconds)
        self.now += seconds


def make_context(http: FakeHttp | None, seconds: float = 10) -> Context:
    """Build the context of a hook call.

    Args:
        http: Client of `ctx.http`.
        seconds: Seconds until the deadline.

    Returns:
        Context.
    """
    return Context(
        api="tes",
        plugin_id="tes.routing.distance",
        principal=Principal(subject="alice"),
        params=EmptyModel(),
        original={},
        deadline=time.monotonic() + seconds,
        state={},
        http=http,
    )


def search_url(query: str) -> str:
    """Get the URL of a Nominatim search, encoded as `requests` does.

    Args:
        query: Free-form query.

    Returns:
        URL with the query string.
    """
    params = {"q": query, "format": "jsonv2", "limit": "1"}
    url = requests.Request("GET", f"{NOMINATIM}/search", params=params).prepare().url
    assert url is not None
    return url


def make_dbip(
    clock: Clock | None = None, interval: float = 0, cache_size: int = 0
) -> DbipProvider:
    """Create the online provider.

    Args:
        clock: Clock of the throttle, back-off and caches; the real one if
            not given.
        interval: Seconds between Nominatim requests.
        cache_size: Size of its caches; by default, nothing is cached.

    Returns:
        Provider.
    """
    if clock is None:
        return DbipProvider(DBIP, NOMINATIM, AGENT, interval, cache_size=cache_size)
    return DbipProvider(
        DBIP,
        NOMINATIM,
        AGENT,
        interval,
        cache_size=cache_size,
        clock=clock,
        sleep=clock.sleep,
    )


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected"),
    [
        (47.5, 8.5, Location(latitude=47.5, longitude=8.5)),
        ("47.5", "-8.5", Location(latitude=47.5, longitude=-8.5)),
        (90, -180, Location(latitude=90, longitude=-180)),
        (90.1, 0, None),
        (0, 180.5, None),
        (True, 0, None),
        ("nan", 0, None),
        ("inf", 0, None),
        ("x", 0, None),
        (None, 0, None),
        ([1], 0, None),
    ],
)
def test_coordinates(latitude: Any, longitude: Any, expected: Location | None) -> None:
    """Coordinates are finite numbers (or numeric strings) within range.

    Args:
        latitude: Latitude.
        longitude: Longitude.
        expected: Location, if valid.
    """
    assert coordinates(latitude, longitude) == expected


def test_offline_provider_locates_addresses(
    database: Path, places: dict[str, Location], addresses: dict[str, str]
) -> None:
    """Addresses in the database have its locations; no network needed.

    Args:
        database: Test database.
        places: Places of its networks.
        addresses: Addresses in its networks.
    """
    provider = OfflineProvider(database)
    try:
        for name, address in addresses.items():
            assert provider.locate(address, make_context(None)) == places[name]
    finally:
        provider.close()


@pytest.mark.parametrize(
    "address", ["4.4.4.4", "5.5.5.5", "6.6.6.6", "7.7.7.7", "not an address"]
)
def test_offline_provider_misses(database: Path, address: str) -> None:
    """Unknown addresses and records without valid location give `None`.

    Args:
        database: Test database.
        address: Address not in it, without location, or invalid.
    """
    provider = OfflineProvider(database)
    try:
        assert provider.locate(address, make_context(None)) is None
    finally:
        provider.close()


def test_offline_provider_refuses_other_files(tmp_path: Path) -> None:
    """A file that is no MaxMind database fails at once (startup).

    Args:
        tmp_path: Pytest fixture.
    """
    path = tmp_path / "not.mmdb"
    path.write_bytes(b"not a database")
    with pytest.raises(ValueError, match="GeoIP database"):
        OfflineProvider(path)


def test_dbip_provider_locates_address() -> None:
    """DB-IP gives the place, Nominatim its coordinates; with a user agent."""
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add(
        "GET",
        search_url("Zurich, Zurich CH"),
        json=[{"lat": "47.3769", "lon": "8.5417"}],
    )
    location = make_dbip().locate(ADDRESS, make_context(http))
    assert location == Location(latitude=47.3769, longitude=8.5417)
    assert [call.url for call in http.calls] == [
        f"{DBIP}/{ADDRESS}",
        search_url("Zurich, Zurich CH"),
    ]
    assert all(
        call.headers == {"User-Agent": AGENT, "Accept": "application/json"}
        for call in http.calls
    )


def test_dbip_provider_falls_back_to_city_and_country() -> None:
    """Places that Nominatim doesn't find are looked up without region."""
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=[])
    http.add("GET", search_url("Zurich, CH"), json=[{"lat": 47, "lon": 8}])
    assert make_dbip().locate(ADDRESS, make_context(http)) == Location(
        latitude=47, longitude=8
    )


def test_dbip_provider_without_region() -> None:
    """Without region, city and country are looked up only."""
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json={**PLACE, "stateProv": None})
    http.add("GET", search_url("Zurich, CH"), json=[{"lat": "1", "lon": "2"}])
    assert make_dbip().locate(ADDRESS, make_context(http)) == Location(
        latitude=1, longitude=2
    )
    assert len(http.calls) == 2


@pytest.mark.parametrize(
    "results",
    [[], [None], [{"lat": "x", "lon": "1"}], [{"lat": "95", "lon": "1"}]],
)
def test_dbip_provider_unknown_place(results: list[Any]) -> None:
    """Places without valid coordinates give `None` (a miss).

    Args:
        results: Nominatim's answer to both queries.
    """
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=results)
    http.add("GET", search_url("Zurich, CH"), json=results)
    assert make_dbip().locate(ADDRESS, make_context(http)) is None


@pytest.mark.parametrize(
    "place",
    [
        {"city": "", "countryCode": "CH"},
        {"city": "Zurich", "countryCode": None},
        {"city": "Zurich", "countryCode": "ZZ"},
    ],
)
def test_dbip_provider_no_place(place: dict[str, Any]) -> None:
    """Addresses without city or country aren't geocoded; a miss.

    Args:
        place: DB-IP's answer.
    """
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=place)
    assert make_dbip().locate(ADDRESS, make_context(http)) is None
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, {"error": "quota exceeded"}),
        (200, ["not", "a", "place"]),
        (429, {}),
        (200, None),
    ],
)
def test_dbip_provider_errors_of_dbip(status: int, body: Any) -> None:
    """Errors of DB-IP fail the lookup; they aren't misses.

    Args:
        status: HTTP status.
        body: Body, as JSON; `None` for a body that is no JSON.
    """
    http = FakeHttp()
    if body is None:
        http.add("GET", f"{DBIP}/{ADDRESS}", status=status, content=b"<html>")
    else:
        http.add("GET", f"{DBIP}/{ADDRESS}", status=status, json=body)
    with pytest.raises(LookupFailed):
        make_dbip().locate(ADDRESS, make_context(http))


@pytest.mark.parametrize(("status", "body"), [(503, []), (200, {"not": "a list"})])
def test_dbip_provider_errors_of_nominatim(status: int, body: Any) -> None:
    """Errors of Nominatim fail the lookup.

    Args:
        status: HTTP status.
        body: Body, as JSON.
    """
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), status=status, json=body)
    with pytest.raises(LookupFailed):
        make_dbip().locate(ADDRESS, make_context(http))


def test_dbip_provider_http_errors() -> None:
    """Errors of the guarded client fail the lookup, with their reason."""
    http = FakeHttp()
    http.add(
        "GET",
        f"{DBIP}/{ADDRESS}",
        error=RequestMaybeSent("timed out", reason="read_timeout"),
    )
    with pytest.raises(LookupFailed, match="read_timeout"):
        make_dbip().locate(ADDRESS, make_context(http))


def test_dbip_provider_needs_http_client() -> None:
    """Without `ctx.http`, the online provider can't work."""
    with pytest.raises(PluginError, match=r"ctx\.http"):
        make_dbip().locate(ADDRESS, make_context(None))


def test_dbip_provider_throttles_nominatim() -> None:
    """Nominatim gets at most one request per interval, per process."""
    clock = Clock()
    provider = make_dbip(clock, interval=1)
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=[])
    http.add("GET", search_url("Zurich, CH"), json=[{"lat": 1, "lon": 2}])
    assert provider.locate(ADDRESS, make_context(http)) is not None
    # the first request at once, the second one an interval later
    assert clock.slept == [1]
    clock.now += 5
    provider.locate(ADDRESS, make_context(http))
    assert clock.slept == [1, 1]


def test_dbip_provider_books_turn_before_asking_dbip() -> None:
    """Without a turn before the deadline, DB-IP isn't asked at all."""
    clock = Clock()
    provider = make_dbip(clock, interval=60)
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=[])
    with pytest.raises(LookupFailed, match="rate limit"):
        provider.locate(ADDRESS, make_context(http, seconds=10))
    # the first lookup used its turn; the second one would come too late
    assert [call.url for call in http.calls] == [
        f"{DBIP}/{ADDRESS}",
        search_url("Zurich, Zurich CH"),
    ]
    with pytest.raises(LookupFailed, match="rate limit"):
        provider.locate("1.1.1.1", make_context(http, seconds=10))
    assert len(http.calls) == 2
    assert clock.slept == []


def test_dbip_provider_waits_outside_the_lock() -> None:
    """A lookup waiting for its turn doesn't block others from booking."""
    provider = make_dbip(interval=0.5)
    http = FakeHttp()
    for index in range(3):
        http.add("GET", f"{DBIP}/9.9.9.{index}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=[{"lat": 1, "lon": 2}])
    # the first lookup takes the first turn at once
    provider.locate("9.9.9.0", make_context(http))
    waiting = threading.Thread(
        target=provider.locate, args=("9.9.9.1", make_context(http))
    )
    waiting.start()  # books the next turn, then waits about 0.5 s
    time.sleep(0.05)
    started = time.monotonic()
    # a third turn would come after its deadline: it fails at once
    with pytest.raises(LookupFailed, match="rate limit"):
        provider.locate("9.9.9.2", make_context(http, seconds=0.2))
    assert time.monotonic() - started < 0.3
    waiting.join(5)
    # DB-IP and Nominatim for the first two; nothing for the third
    assert len(http.calls) == 4


def test_dbip_provider_caches_places_and_coordinates() -> None:
    """Places (by address) and coordinates (by place) are asked for once."""
    clock = Clock()
    provider = make_dbip(clock, cache_size=10)
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", f"{DBIP}/1.1.1.1", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=[{"lat": 1, "lon": 2}])
    here = Location(latitude=1, longitude=2)
    assert provider.locate(ADDRESS, make_context(http)) == here
    assert provider.locate(ADDRESS, make_context(http)) == here
    assert provider.locate("1.1.1.1", make_context(http)) == here
    assert [call.url for call in http.calls] == [
        f"{DBIP}/{ADDRESS}",
        search_url("Zurich, Zurich CH"),
        f"{DBIP}/1.1.1.1",
    ]
    clock.now += 86401
    provider.locate(ADDRESS, make_context(http))
    assert len(http.calls) == 5


def test_dbip_provider_keeps_place_if_nominatim_fails() -> None:
    """A failure of Nominatim doesn't cost another request to DB-IP."""
    provider = make_dbip(cache_size=10)
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), status=503, json=[])
    with pytest.raises(LookupFailed):
        provider.locate(ADDRESS, make_context(http))
    with pytest.raises(LookupFailed):
        provider.locate(ADDRESS, make_context(http))
    assert [call.url for call in http.calls].count(f"{DBIP}/{ADDRESS}") == 1


@pytest.mark.parametrize(
    ("status", "body"), [(200, {"error": "over query limit"}), (429, {})]
)
def test_dbip_provider_backs_off(status: int, body: Any) -> None:
    """After DB-IP refused a lookup, it is asked nothing for a while.

    Args:
        status: HTTP status of the refusal.
        body: Body of the refusal.
    """
    clock = Clock()
    provider = make_dbip(clock)
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", status=status, json=body)
    with pytest.raises(LookupFailed, match=r"refused|429"):
        provider.locate(ADDRESS, make_context(http))
    clock.now += 599
    with pytest.raises(LookupFailed, match="backing off"):
        provider.locate(ADDRESS, make_context(http))
    assert len(http.calls) == 1
    clock.now += 2
    with pytest.raises(LookupFailed):
        provider.locate(ADDRESS, make_context(http))
    assert len(http.calls) == 2


def test_dbip_provider_other_errors_dont_back_off() -> None:
    """Other failures of DB-IP (e.g., 503) don't stop further lookups."""
    provider = make_dbip()
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", status=503, json={})
    for _ in range(2):
        with pytest.raises(LookupFailed, match="503"):
            provider.locate(ADDRESS, make_context(http))
    assert len(http.calls) == 2


def test_offline_provider_fails_after_close(database: Path) -> None:
    """Lookups after `close()` fail; they aren't misses.

    Args:
        database: Test database.
    """
    provider = OfflineProvider(database)
    provider.close()
    with pytest.raises(LookupFailed, match="closed"):
        provider.locate(ADDRESS, make_context(None))


def test_offline_provider_fails_if_closed_during_lookup(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader closed while it reads fails the lookup, too.

    Args:
        database: Test database.
        monkeypatch: Pytest fixture.
    """
    provider = OfflineProvider(database)
    reader = provider._reader

    def get(address: str) -> Any:
        provider.close()
        return reader.get(address)

    monkeypatch.setattr(provider, "_reader", MagicMock(get=get, close=reader.close))
    with pytest.raises(LookupFailed, match="closed"):
        provider.locate(ADDRESS, make_context(None))


def test_cache_hits_and_expires() -> None:
    """Values are kept for their TTL."""
    clock = Clock()
    cache: TtlCache[str, Location | None] = TtlCache(size=10, clock=clock)
    here = Location(latitude=1, longitude=2)
    assert cache.get("9.9.9.9") == (False, None)
    cache.put("9.9.9.9", here, 100)
    cache.put("4.4.4.4", None, 10)
    assert cache.get("9.9.9.9") == (True, here)
    assert cache.get("4.4.4.4") == (True, None)
    clock.now += 11
    assert cache.get("4.4.4.4") == (False, None)
    assert cache.get("9.9.9.9") == (True, here)
    clock.now += 90
    assert cache.get("9.9.9.9") == (False, None)


def test_cache_evicts_least_recently_used() -> None:
    """Beyond its size, the entry used least recently goes."""
    cache: TtlCache[str, Location | None] = TtlCache(size=2)
    first, second = Location(latitude=1, longitude=1), Location(latitude=2, longitude=2)
    cache.put("1.0.0.1", first, 100)
    cache.put("2.0.0.2", second, 100)
    assert cache.get("1.0.0.1") == (True, first)
    cache.put("3.0.0.3", None, 100)
    assert cache.get("2.0.0.2") == (False, None)
    assert cache.get("1.0.0.1") == (True, first)
    assert cache.get("3.0.0.3") == (True, None)


@pytest.mark.parametrize(("size", "ttl"), [(0, 100), (10, 0)])
def test_cache_can_be_turned_off(size: int, ttl: int) -> None:
    """Size 0 turns the cache off; TTL 0 keeps nothing.

    Args:
        size: Size of the cache.
        ttl: TTL of the value.
    """
    cache: TtlCache[str, Location | None] = TtlCache(size=size)
    cache.put("9.9.9.9", Location(latitude=1, longitude=1), ttl)
    assert cache.get("9.9.9.9") == (False, None)
