"""Tests of the plugin `tes.routing.distance`.

Unit tests call the hook with a context of their own; the other tests use
FOCA's plugin test kit (`foca.plugins.testing`): the contract checks, a
harness that runs the plugin through the real engine (with `FakeHttp` for
the online provider), and a pipeline that loads the plugin through its
installed entry point, as gateway apps do. Hosts are IP addresses, so that
no test needs DNS, except where name resolution is the subject.
"""

from collections.abc import Iterator, Sequence
from importlib.metadata import entry_points
from pathlib import Path
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock

from foca.plugins import Backend, Context, Location, NotApplicable, Principal
from foca.plugins.base import EmptyModel, check_plugin_class
from foca.plugins.config import PluginsConfig
from foca.plugins.engine import PluginHost
from foca.plugins.testing import (
    FakeHttp,
    PluginHarness,
    check_plugin_contract,
    make_backend,
    make_inbound,
)
from pydantic import ValidationError
import pytest
import requests

from foca_plugins.tes.routing.distance import Distance, DistanceConfig, geo
from foca_plugins.tes.routing.distance.cache import TtlCache
from foca_plugins.tes.routing.distance.geo import distance_km
from foca_plugins.tes.routing.distance.providers import LookupFailed

PLUGIN_ID = "tes.routing.distance"


def make_task(*hosts: str) -> dict[str, Any]:
    """Build a task with one input per host.

    Args:
        hosts: Hosts of the input URLs.

    Returns:
        Task.
    """
    return {
        "executors": [{"image": "alpine", "command": ["true"]}],
        "inputs": [
            {"url": f"https://{host}/data/{index}", "path": f"/in/{index}"}
            for index, host in enumerate(hosts)
        ],
    }


def backend(name: str, host: str, location: Location | None = None) -> Backend:
    """Build a candidate backend.

    Args:
        name: Identifier.
        host: Host of its URL (an IP address, in brackets for IPv6).
        location: Location configured by the admin.

    Returns:
        Backend.
    """
    return make_backend(name, f"https://{host}/ga4gh/tes/v1", location=location)


def make_context(task: Any, seconds: float = 10) -> Context:
    """Build the context of a hook call.

    Args:
        task: Task, as submitted (`ctx.original`).
        seconds: Seconds until the deadline.

    Returns:
        Context, without HTTP client.
    """
    return Context(
        api="tes",
        plugin_id=PLUGIN_ID,
        principal=Principal(subject="alice"),
        params=EmptyModel(),
        original=task,
        deadline=time.monotonic() + seconds,
        state={},
        request_id="r1",
    )


def ids(backends: Sequence[Backend]) -> list[str]:
    """Get the identifiers of backends.

    Args:
        backends: Backends.

    Returns:
        Identifiers, in order.
    """
    return [b.id for b in backends]


@pytest.fixture
def plugin(database: Path) -> Iterator[Distance]:
    """Create the plugin with the offline provider and the test database.

    Args:
        database: Test database.

    Yields:
        Plugin, closed afterwards.
    """
    plugin = Distance(PLUGIN_ID, DistanceConfig(database=database))
    yield plugin
    plugin.close()


@pytest.fixture
def world(addresses: dict[str, str]) -> list[Backend]:
    """Build backends in Sydney, Mountain View and Helsinki, in that order.

    Args:
        addresses: Addresses in the networks of the test database.

    Returns:
        Backends.
    """
    return [
        backend("sydney", addresses["sydney"]),
        backend("mountain_view", addresses["mountain_view"]),
        backend("helsinki", f"[{addresses['helsinki']}]"),
    ]


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is Distance


def test_plugin_class_is_valid() -> None:
    """FOCA accepts the class for TES only; it routes only."""
    assert check_plugin_class(Distance, PLUGIN_ID, "tes") is Distance
    with pytest.raises(ValueError, match="does not support API"):
        check_plugin_class(Distance, PLUGIN_ID, "wes")
    assert Distance.phases() == ("route",)
    assert Distance.capabilities == frozenset({"routing-by-data-locality"})
    title, description = Distance.summary()
    assert title == "Route to the backends closest to the task's inputs."
    assert description.startswith("Ranks the candidate backends")
    recipients = [transfer.split(":")[0] for transfer in Distance.data_transfers]
    assert recipients == [
        "db-ip.com (api.db-ip.com)",
        "OpenStreetMap Nominatim (or the instance the admin configures)",
    ]
    # The lookups are cached per process, not in the plugin store
    assert Distance.data_stored == ()


def test_plugin_meets_contract(
    database: Path, world: list[Backend], addresses: dict[str, str]
) -> None:
    """The plugin passes FOCA's contract checks, with a task it can route.

    Args:
        database: Test database.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    check_plugin_contract(
        Distance,
        config={"database": str(database)},
        plugin_id=PLUGIN_ID,
        inbound=make_inbound(make_task(addresses["zurich"])),
        candidates=world,
    )


def test_config_defaults(database: Path) -> None:
    """Offline lookups, with backends, a day's cache and generic settings.

    Args:
        database: Test database.
    """
    config = DistanceConfig(database=database)
    assert config.provider == "offline"
    assert config.locate_backends is True
    assert config.cache_ttl == 86400
    assert config.dbip_url == "https://api.db-ip.com/v2/free"
    assert config.nominatim_url == "https://nominatim.openstreetmap.org"
    assert config.user_agent == "foca-plugin-tes-routing-distance"
    assert config.resolve_timeout == 2
    assert config.resolve_threads == 16
    assert config.dbip_max_lookups == 5
    assert config.cache_failure_ttl == 60


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"database": "/does/not/exist.mmdb"},
        {"provider": "geolite"},
        {"provider": "dbip", "dbip_url": "http://api.db-ip.com/v2/free"},
        {"provider": "dbip", "nominatim_url": "ftp://nominatim.example.org"},
        {"provider": "dbip", "user_agent": ""},
        {"provider": "dbip", "user_agent": " gw/1"},
        {"provider": "dbip", "user_agent": "gw/1\r\nX-Evil: 1"},
        {"provider": "dbip", "user_agent": "gw/1\u00e9"},
        {"provider": "dbip", "resolve_threads": 0},
        {"provider": "dbip", "dbip_max_lookups": 0},
        {"provider": "dbip", "max_hosts": 0},
        {"provider": "dbip", "cache_ttl": -1},
        {"provider": "dbip", "other": "value"},
    ],
)
def test_config_is_validated(data: dict[str, Any]) -> None:
    """Invalid or unknown settings are refused; offline needs a database.

    Args:
        data: Invalid settings.
    """
    with pytest.raises(ValidationError):
        DistanceConfig.model_validate(data)


def test_config_strips_trailing_slashes() -> None:
    """Base URLs are kept without trailing slash."""
    config = DistanceConfig(
        provider="dbip",
        dbip_url="https://geo.example.org/v2/free/",
        nominatim_url="https://osm.example.org/",
    )
    assert config.dbip_url == "https://geo.example.org/v2/free"
    assert config.nominatim_url == "https://osm.example.org"


def test_on_route_ranks_by_distance(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
) -> None:
    """Backends closest to the input come first; counts are recorded.

    Args:
        plugin: Plugin.
        world: Backends in Sydney, Mountain View and Helsinki.
        addresses: Addresses in the networks of the test database.
    """
    ctx = make_context(make_task(addresses["zurich"]))
    assert ids(plugin.on_route(world, ctx)) == ["helsinki", "mountain_view", "sydney"]
    assert ctx.annotations == {
        "inputs": 1,
        "input_hosts": 1,
        "located_inputs": 1,
        "located_backends": 3,
        "lookups": 4,
        "cache_hits": 0,
        "failed_lookups": 0,
        "skipped_lookups": 0,
    }


def test_on_route_sums_distances_over_inputs(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
    places: dict[str, Location],
) -> None:
    """Each input URL adds its distance, also several on the same host.

    Args:
        plugin: Plugin.
        world: Backends in Sydney, Mountain View and Helsinki.
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.
    """
    inputs = ("sydney", "sydney", "zurich")
    task = make_task(*(addresses[name] for name in inputs))

    def total(place: str) -> float:
        """Sum the distances of a place to the inputs.

        Args:
            place: Name of the place.

        Returns:
            Distance, in kilometres.
        """
        return sum(distance_km(places[place], places[name]) for name in inputs)

    expected = sorted(("sydney", "mountain_view", "helsinki"), key=total)
    ctx = make_context(task)
    assert ids(plugin.on_route(world, ctx)) == expected
    assert ctx.annotations["inputs"] == 3
    assert ctx.annotations["input_hosts"] == 2


def test_configured_locations_come_first(
    plugin: Distance, addresses: dict[str, str], places: dict[str, Location]
) -> None:
    """A backend's configured location wins over its address's.

    Args:
        plugin: Plugin.
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.
    """
    candidates = [
        backend("far", addresses["zurich"], location=places["sydney"]),
        backend("near", addresses["sydney"], location=places["zurich"]),
    ]
    ctx = make_context(make_task(addresses["zurich"]))
    assert ids(plugin.on_route(candidates, ctx)) == ["near", "far"]
    # the input only
    assert ctx.annotations["lookups"] == 1


def test_unlocated_backends_follow_in_their_order(
    plugin: Distance, addresses: dict[str, str]
) -> None:
    """Backends without location are kept, after the located ones.

    Args:
        plugin: Plugin.
        addresses: Addresses in the networks of the test database.
    """
    candidates = [
        backend("unknown", "4.4.4.4"),
        backend("far", addresses["sydney"]),
        backend("private", "10.0.0.1"),
        backend("near", addresses["zurich"]),
        backend("hostless", "127.0.0.1"),
    ]
    ctx = make_context(make_task(f"[{addresses['helsinki']}]"))
    assert ids(plugin.on_route(candidates, ctx)) == [
        "near",
        "far",
        "unknown",
        "private",
        "hostless",
    ]
    assert ctx.annotations["located_backends"] == 2


def test_ties_keep_the_candidates_order(
    plugin: Distance, addresses: dict[str, str], places: dict[str, Location]
) -> None:
    """Backends at the same distance keep their order.

    Args:
        plugin: Plugin.
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.
    """
    candidates = [
        backend(name, addresses["sydney"], location=places["helsinki"])
        for name in ("c", "a", "b")
    ]
    ctx = make_context(make_task(addresses["zurich"]))
    assert ids(plugin.on_route(candidates, ctx)) == ["c", "a", "b"]


def test_backends_not_looked_up_if_turned_off(
    database: Path, world: list[Backend], addresses: dict[str, str]
) -> None:
    """With `locate_backends: false`, only configured locations count.

    Args:
        database: Test database.
        world: Backends without configured locations.
        addresses: Addresses in the networks of the test database.
    """
    plugin = Distance(
        PLUGIN_ID, DistanceConfig(database=database, locate_backends=False)
    )
    try:
        with pytest.raises(NotApplicable, match="No candidate backend"):
            plugin.on_route(world, make_context(make_task(addresses["zurich"])))
    finally:
        plugin.close()


@pytest.mark.parametrize(
    ("task", "reason"),
    [
        ({"executors": []}, "no input URLs"),
        (make_task("4.4.4.4", "10.0.0.1"), "No input of the task"),
        ({"inputs": [{"url": "file:///data/x"}]}, "No input of the task"),
    ],
)
def test_not_applicable_without_located_inputs(
    plugin: Distance, world: list[Backend], task: Any, reason: str
) -> None:
    """Tasks without inputs that can be located aren't routed by distance.

    Args:
        plugin: Plugin.
        world: Backends.
        task: Task.
        reason: Part of the reason.
    """
    with pytest.raises(NotApplicable, match=reason):
        plugin.on_route(world, make_context(task))


def test_not_applicable_without_located_backends(
    plugin: Distance, addresses: dict[str, str]
) -> None:
    """Without any located candidate, there is nothing to rank.

    Args:
        plugin: Plugin.
        addresses: Addresses in the networks of the test database.
    """
    candidates = [backend("a", "4.4.4.4"), backend("b", "10.0.0.2")]
    with pytest.raises(NotApplicable, match="No candidate backend"):
        plugin.on_route(candidates, make_context(make_task(addresses["zurich"])))


def test_input_hosts_are_capped(
    database: Path, world: list[Backend], addresses: dict[str, str]
) -> None:
    """Only the first `max_hosts` input hosts are located.

    Args:
        database: Test database.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    plugin = Distance(PLUGIN_ID, DistanceConfig(database=database, max_hosts=1))
    try:
        ctx = make_context(make_task(addresses["zurich"], addresses["sydney"]))
        assert ids(plugin.on_route(world, ctx))[0] == "helsinki"
        assert ctx.annotations["input_hosts"] == 2
        assert ctx.annotations["located_inputs"] == 1
    finally:
        plugin.close()


def test_host_names_are_resolved(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Input hosts given by name are resolved to their public address.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    zurich = addresses["zurich"]
    resolved: list[str] = []

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Resolve every host to the address in Zurich.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        resolved.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (zurich, 443))]

    monkeypatch.setattr(geo.socket, "getaddrinfo", getaddrinfo)
    ctx = make_context(make_task("data.example.org"))
    assert ids(plugin.on_route(world, ctx))[0] == "helsinki"
    assert resolved == ["data.example.org"]


def test_lookups_are_cached(
    plugin: Distance, world: list[Backend], addresses: dict[str, str]
) -> None:
    """Addresses and misses are looked up once per process, until expiry.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    # 2 inputs (one in Zurich, one unknown: a miss) and 3 backends
    task = make_task(addresses["zurich"], "4.4.4.4")
    first = make_context(task)
    plugin.on_route(world, first)
    assert (first.annotations["lookups"], first.annotations["cache_hits"]) == (5, 0)
    second = make_context(task)
    plugin.on_route(world, second)
    assert (second.annotations["lookups"], second.annotations["cache_hits"]) == (
        0,
        5,
    )


def test_failed_lookups_are_cached_briefly(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing lookup leaves the address unlocated for `cache_failure_ttl`.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    provider = plugin.provider
    real = provider.locate
    failed: list[str] = []

    def flaky(address: str, ctx: Context) -> Location | None:
        """Fail for the address in Sydney.

        Args:
            address: Address.
            ctx: Context.

        Returns:
            Location, if any.

        Raises:
            LookupFailed: For the address in Sydney.
        """
        if address == addresses["sydney"]:
            failed.append(address)
            raise LookupFailed("unavailable")
        return real(address, ctx)

    now = [1000.0]
    plugin.cache = TtlCache(plugin.settings.cache_size, clock=lambda: now[0])
    monkeypatch.setattr(provider, "locate", flaky)
    task = make_task(addresses["zurich"])
    ctx = make_context(task)
    assert ids(plugin.on_route(world, ctx)) == ["helsinki", "mountain_view", "sydney"]
    assert ctx.annotations["located_backends"] == 2
    assert ctx.annotations["failed_lookups"] == 1
    plugin.on_route(world, make_context(task))
    assert len(failed) == 1
    now[0] += plugin.settings.cache_failure_ttl + 1
    plugin.on_route(world, make_context(task))
    assert len(failed) == 2


def test_transient_failures_are_not_cached(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lookups that failed for lack of time are tried again in the next call.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    provider = plugin.provider
    real = provider.locate
    failed: list[str] = []

    def busy(address: str, ctx: Context) -> Location | None:
        """Have no time for the address in Sydney.

        Args:
            address: Address.
            ctx: Context.

        Returns:
            Location, if any.

        Raises:
            LookupFailed: For the address in Sydney (transient).
        """
        if address == addresses["sydney"]:
            failed.append(address)
            raise LookupFailed("no time", transient=True)
        return real(address, ctx)

    monkeypatch.setattr(provider, "locate", busy)
    task = make_task(addresses["zurich"])
    for _ in range(2):
        ctx = make_context(task)
        plugin.on_route(world, ctx)
        assert ctx.annotations["failed_lookups"] == 1
    assert len(failed) == 2


def test_misses_expire(
    plugin: Distance, world: list[Backend], addresses: dict[str, str]
) -> None:
    """Addresses without location are kept for `cache_miss_ttl`, located ones longer.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    now = [1000.0]
    settings = plugin.settings
    plugin.cache = TtlCache(settings.cache_size, clock=lambda: now[0])
    # one input in Zurich, one unknown (a miss); 3 backends
    assert len(world) == 3
    task = make_task(addresses["zurich"], "4.4.4.4")
    plugin.on_route(world, make_context(task))
    now[0] += settings.cache_miss_ttl + 1
    ctx = make_context(task)
    plugin.on_route(world, ctx)
    # only the miss is looked up again
    assert (ctx.annotations["lookups"], ctx.annotations["cache_hits"]) == (1, 4)
    now[0] += settings.cache_ttl
    ctx = make_context(task)
    plugin.on_route(world, ctx)
    assert (ctx.annotations["lookups"], ctx.annotations["cache_hits"]) == (5, 0)


def test_cache_can_be_turned_off(
    database: Path, world: list[Backend], addresses: dict[str, str]
) -> None:
    """With `cache_size: 0`, every call looks up every address.

    Args:
        database: Test database.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    plugin = Distance(PLUGIN_ID, DistanceConfig(database=database, cache_size=0))
    try:
        task = make_task(addresses["zurich"])
        for _ in range(2):
            ctx = make_context(task)
            plugin.on_route(world, ctx)
            assert (ctx.annotations["lookups"], ctx.annotations["cache_hits"]) == (
                4,
                0,
            )
    finally:
        plugin.close()


def test_private_addresses_never_reach_the_provider(
    plugin: Distance, addresses: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Private, in-cluster and mapped private addresses aren't looked up.

    Args:
        plugin: Plugin.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    asked: list[str] = []
    real = plugin.provider.locate

    def spy(address: str, ctx: Context) -> Location | None:
        """Record the address, then look it up.

        Args:
            address: Address.
            ctx: Context.

        Returns:
            Location, if any.
        """
        asked.append(address)
        return real(address, ctx)

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Resolve every name to private addresses only.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::1", 443, 0, 0)),
        ]

    monkeypatch.setattr(plugin.provider, "locate", spy)
    monkeypatch.setattr(geo.socket, "getaddrinfo", getaddrinfo)
    task = make_task(
        addresses["zurich"],
        "10.0.0.7",
        "[::ffff:10.0.0.8]",
        "192.168.1.1",
        "data.internal.example",
    )
    candidates = [
        backend("private", "10.0.0.9"),
        backend("svc", "tes.default.svc.cluster.local"),
        backend("zurich", addresses["zurich"]),
    ]
    assert ids(plugin.on_route(candidates, make_context(task)))[0] == "zurich"
    assert asked == [addresses["zurich"]]


def test_slow_name_servers_dont_hold_the_call(
    database: Path,
    world: list[Backend],
    addresses: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names that hang cost one `resolve_timeout` per call, with few threads.

    Args:
        database: Test database.
        world: Backends.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    release = threading.Event()
    asked: list[str] = []

    def hang(host: str, *args: Any, **kwargs: Any) -> Any:
        """Never answer (until the test ends).

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Nothing in time.
        """
        asked.append(host)
        release.wait(10)
        return []

    monkeypatch.setattr(geo.socket, "getaddrinfo", hang)
    plugin = Distance(
        PLUGIN_ID,
        DistanceConfig(database=database, resolve_timeout=0.2, resolve_threads=4),
    )
    names = [f"h{i}.slow.example" for i in range(20)]
    task = make_task(addresses["zurich"], *names)
    before = threading.active_count()
    try:
        started = time.monotonic()
        ctx = make_context(task)
        assert ids(plugin.on_route(world, ctx))[0] == "helsinki"
        assert time.monotonic() - started < 1
        # again: a call starts at most a quarter of the threads (1), and
        # waits for the hanging name of the first call, not resolving it again
        plugin.on_route(world, make_context(task))
        assert time.monotonic() - started < 2
        assert asked == names[:2]
        assert threading.active_count() - before <= 2
    finally:
        release.set()
        plugin.close()


def test_no_resolution_after_deadline(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the call's time is up, no name is resolved and nothing looked up.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    asked: list[str] = []
    monkeypatch.setattr(
        geo.socket, "getaddrinfo", lambda host, *a, **k: asked.append(host)
    )
    ctx = make_context(make_task(addresses["zurich"], "data.example.org"), -1)
    with pytest.raises(NotApplicable, match="No input"):
        plugin.on_route(world, ctx)
    assert asked == []
    assert ctx.annotations["skipped_lookups"] == 1
    assert ctx.annotations["lookups"] == 0


def test_setup_is_idempotent(database: Path) -> None:
    """Setting up again closes the previous provider; close() closes it.

    Args:
        database: Test database.
    """
    plugin = Distance(PLUGIN_ID, DistanceConfig(database=database))
    first = plugin.provider = MagicMock(wraps=plugin.provider)
    plugin.setup(DistanceConfig(database=database))
    first.close.assert_called_once()
    second = plugin.provider = MagicMock(wraps=plugin.provider)
    plugin.close()
    second.close.assert_called_once()
    # the provider stays: hooks still running at shutdown fail cleanly
    assert plugin.provider is second


def test_setup_refuses_offline_without_database() -> None:
    """Settings that bypassed validation can't set up the offline provider."""
    config = DistanceConfig.model_construct(provider="offline", database=None)
    with pytest.raises(ValueError, match="needs 'database'"):
        Distance(PLUGIN_ID, config)


def test_lookups_after_close_fail(
    plugin: Distance, world: list[Backend], addresses: dict[str, str]
) -> None:
    """Hooks that run after `close()` fail their lookups, without errors.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    plugin.close()
    ctx = make_context(make_task(addresses["zurich"]))
    with pytest.raises(NotApplicable, match="No input"):
        plugin.on_route(world, ctx)
    assert ctx.annotations["failed_lookups"] == 1


def test_online_provider_through_engine(
    world: list[Backend], addresses: dict[str, str], places: dict[str, Location]
) -> None:
    """With `provider: dbip`, lookups go through `ctx.http`, as configured.

    Args:
        world: Backends.
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.
    """
    http = dbip_http(addresses, places)
    harness = PluginHarness(
        Distance,
        config={"provider": "dbip", "nominatim_interval": 0, "user_agent": "gw/1"},
        plugin_id=PLUGIN_ID,
        http=http,
    )
    routed = harness.run_route(world, make_inbound(make_task(addresses["zurich"])))
    assert ids(routed) == ["helsinki", "mountain_view", "sydney"]
    assert [e.outcome for e in harness.executions] == ["ok"]
    assert len(http.calls) == 8
    assert {(call.headers or {}).get("User-Agent") for call in http.calls} == {"gw/1"}
    # the second task with inputs in Zurich: all from the caches
    harness.run_route(world, make_inbound(make_task(addresses["zurich"])))
    assert len(http.calls) == 8
    assert harness.executions[0].annotations["cache_hits"] == 4


def test_online_lookups_are_capped_per_call(
    world: list[Backend], addresses: dict[str, str], places: dict[str, Location]
) -> None:
    """With `dbip`, inputs and backends each get `dbip_max_lookups` lookups.

    Args:
        world: Backends.
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.
    """
    http = dbip_http(addresses, places)
    harness = PluginHarness(
        Distance,
        config={"provider": "dbip", "nominatim_interval": 0, "dbip_max_lookups": 2},
        plugin_id=PLUGIN_ID,
        http=http,
    )
    assert len(world) == 3
    harness.run_route(world, make_inbound(make_task(addresses["zurich"])))
    (execution,) = harness.executions
    # the input, then 2 of the 3 backends (a budget per batch)
    assert execution.annotations["lookups"] == 3
    assert execution.annotations["skipped_lookups"] == 1
    # DB-IP and Nominatim for each lookup
    assert len(http.calls) == 6


def dbip_http(addresses: dict[str, str], places: dict[str, Location]) -> FakeHttp:
    """Build answers of DB-IP and Nominatim for the test networks.

    Args:
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.

    Returns:
        Fake HTTP client.
    """
    http = FakeHttp()
    for name, address in addresses.items():
        city = name.replace("_", " ").title()
        http.add(
            "GET",
            f"https://api.db-ip.com/v2/free/{address.replace(':', '%3A')}",
            json={"city": city, "countryCode": "XX"},
        )
        params = {"q": f"{city}, XX", "format": "jsonv2", "limit": "1"}
        url = (
            requests.Request(
                "GET", "https://nominatim.openstreetmap.org/search", params=params
            )
            .prepare()
            .url
        )
        assert url is not None
        location = places[name]
        http.add(
            "GET", url, json=[{"lat": location.latitude, "lon": location.longitude}]
        )
    return http


def test_not_applicable_through_engine(database: Path, world: list[Backend]) -> None:
    """Through the engine, a skippable step leaves the candidates as they are.

    Args:
        database: Test database.
        world: Backends.
    """
    harness = PluginHarness(
        Distance,
        config={"database": str(database)},
        plugin_id=PLUGIN_ID,
        on_failure="skip",
    )
    assert harness.run_route(world, make_inbound(make_task())) == world
    assert [e.outcome for e in harness.executions] == ["not_applicable", "skipped"]


def test_pipeline_runs_plugin(
    database: Path, world: list[Backend], addresses: dict[str, str]
) -> None:
    """The engine loads the installed plugin and routes with it.

    Args:
        database: Test database.
        world: Backends.
        addresses: Addresses in the networks of the test database.
    """
    conf = PluginsConfig.model_validate(
        {
            "available": {PLUGIN_ID: {"config": {"database": str(database)}}},
            "pipeline": [{"id": "route", "alternatives": [{"plugin": PLUGIN_ID}]}],
        }
    )
    host = PluginHost(conf, api="tes")
    try:
        run = host.start(
            make_inbound(make_task(addresses["zurich"])),
            Principal(subject="alice"),
            request_id="r1",
        )
        run.request()
        assert ids(run.route(world)) == ["helsinki", "mountain_view", "sydney"]
    finally:
        host.close()


def test_input_names_cant_hold_up_backends(
    database: Path, addresses: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backend names resolve even while names of inputs hang in all threads.

    Args:
        database: Test database.
        addresses: Addresses in the networks of the test database.
        monkeypatch: Pytest fixture.
    """
    release = threading.Event()

    def getaddrinfo(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Hang for names of inputs; resolve backends to Helsinki.

        Args:
            host: Host name.
            args: Other arguments.
            kwargs: Other keyword arguments.

        Returns:
            Address info.
        """
        if host.endswith(".slow.example"):
            release.wait(10)
            return []
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (addresses["helsinki"], 443))
        ]

    monkeypatch.setattr(geo.socket, "getaddrinfo", getaddrinfo)
    plugin = Distance(
        PLUGIN_ID,
        DistanceConfig(database=database, resolve_timeout=0.1, resolve_threads=4),
    )
    try:
        # four tasks take the four threads of input names (one per call)
        for index in range(4):
            task = make_task(addresses["zurich"], f"h{index}.slow.example")
            plugin.on_route([backend("a", addresses["sydney"])], make_context(task))
        candidates = [
            backend("sydney", addresses["sydney"]),
            backend("helsinki", "tes.helsinki.example"),
        ]
        task = make_task(addresses["zurich"], "h9.slow.example")
        ctx = make_context(task)
        assert ids(plugin.on_route(candidates, ctx)) == ["helsinki", "sydney"]
        assert ctx.annotations["located_backends"] == 2
    finally:
        release.set()
        plugin.close()


def test_failures_dont_replace_locations(
    plugin: Distance,
    world: list[Backend],
    addresses: dict[str, str],
    places: dict[str, Location],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed lookup keeps a location that a concurrent call found.

    Args:
        plugin: Plugin.
        world: Backends.
        addresses: Addresses in the networks of the test database.
        places: Places of the networks.
        monkeypatch: Pytest fixture.
    """
    sydney = addresses["sydney"]
    real = plugin.provider.locate

    def raced(address: str, ctx: Context) -> Location | None:
        """Fail for Sydney after a concurrent call located it.

        Args:
            address: Address.
            ctx: Context.

        Returns:
            Location, if any.

        Raises:
            LookupFailed: For the address in Sydney.
        """
        if address == sydney:
            plugin.cache.put(sydney, places["sydney"], 100)
            raise LookupFailed("unavailable")
        return real(address, ctx)

    monkeypatch.setattr(plugin.provider, "locate", raced)
    plugin.on_route(world, make_context(make_task(addresses["zurich"])))
    assert plugin.cache.get(sydney) == (True, places["sydney"])


def test_bucket_inputs_are_not_located(
    plugin: Distance, world: list[Backend], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bucket names of `s3` or `gs` URLs aren't resolved as hosts.

    Args:
        plugin: Plugin.
        world: Backends.
        monkeypatch: Pytest fixture.
    """
    asked: list[str] = []
    monkeypatch.setattr(
        geo.socket, "getaddrinfo", lambda host, *a, **k: asked.append(host)
    )
    task = {"inputs": [{"url": "s3://data.example.org/key"}]}
    with pytest.raises(NotApplicable, match="No input"):
        plugin.on_route(world, make_context(task))
    assert asked == []
