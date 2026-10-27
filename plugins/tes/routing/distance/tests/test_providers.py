"""Tests of the location providers and the cache of `tes.routing.distance`."""

from pathlib import Path
import time
from typing import Any

from foca.plugins import Context, Location, PluginError, Principal
from foca.plugins.base import EmptyModel
from foca.plugins.testing import FakeHttp
from foca.utils.http import RequestMaybeSent
import pytest
import requests

from foca_plugins.tes.routing.distance.providers import (
    DbipProvider,
    LocationCache,
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


def make_dbip(clock: Clock | None = None, interval: float = 0) -> DbipProvider:
    """Create the online provider.

    Args:
        clock: Clock of the throttle; the real one if not given.
        interval: Seconds between Nominatim requests.

    Returns:
        Provider.
    """
    if clock is None:
        return DbipProvider(DBIP, NOMINATIM, AGENT, interval)
    return DbipProvider(
        DBIP, NOMINATIM, AGENT, interval, clock=clock, sleep=clock.sleep
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


def test_dbip_provider_throttle_respects_deadline() -> None:
    """A wait beyond the deadline fails the lookup instead of sleeping."""
    clock = Clock()
    provider = make_dbip(clock, interval=60)
    http = FakeHttp()
    http.add("GET", f"{DBIP}/{ADDRESS}", json=PLACE)
    http.add("GET", search_url("Zurich, Zurich CH"), json=[])
    with pytest.raises(LookupFailed, match="rate limit"):
        provider.locate(ADDRESS, make_context(http, seconds=10))
    assert clock.slept == []


def test_cache_hits_and_expires() -> None:
    """Locations are kept for the TTL, misses for theirs."""
    clock = Clock()
    cache = LocationCache(size=10, ttl=100, miss_ttl=10, clock=clock)
    here = Location(latitude=1, longitude=2)
    assert cache.get("9.9.9.9") == (False, None)
    cache.put("9.9.9.9", here)
    cache.put("4.4.4.4", None)
    assert cache.get("9.9.9.9") == (True, here)
    assert cache.get("4.4.4.4") == (True, None)
    clock.now += 11
    assert cache.get("4.4.4.4") == (False, None)
    assert cache.get("9.9.9.9") == (True, here)
    clock.now += 90
    assert cache.get("9.9.9.9") == (False, None)


def test_cache_evicts_least_recently_used() -> None:
    """Beyond its size, the entry used least recently goes."""
    cache = LocationCache(size=2, ttl=100, miss_ttl=100)
    first, second = Location(latitude=1, longitude=1), Location(latitude=2, longitude=2)
    cache.put("1.0.0.1", first)
    cache.put("2.0.0.2", second)
    assert cache.get("1.0.0.1") == (True, first)
    cache.put("3.0.0.3", None)
    assert cache.get("2.0.0.2") == (False, None)
    assert cache.get("1.0.0.1") == (True, first)
    assert cache.get("3.0.0.3") == (True, None)


@pytest.mark.parametrize(
    ("size", "ttl", "miss_ttl", "value"),
    [
        (0, 100, 100, Location(latitude=1, longitude=1)),
        (10, 0, 100, Location(latitude=1, longitude=1)),
        (10, 100, 0, None),
    ],
)
def test_cache_can_be_turned_off(
    size: int, ttl: int, miss_ttl: int, value: Location | None
) -> None:
    """Size 0 turns the cache off; TTL 0 keeps no locations or misses.

    Args:
        size: Size of the cache.
        ttl: TTL of locations.
        miss_ttl: TTL of misses.
        value: Value to cache.
    """
    cache = LocationCache(size=size, ttl=ttl, miss_ttl=miss_ttl)
    cache.put("9.9.9.9", value)
    assert cache.get("9.9.9.9") == (False, None)
