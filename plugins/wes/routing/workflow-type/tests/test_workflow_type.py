"""Tests of the plugin `wes.routing.workflow-type`.

Unit tests call the hook with a context of their own and FOCA's fake HTTP
client; the other tests use FOCA's plugin test kit
(`foca.plugins.testing`): the contract checks, a harness that runs the
plugin through the real engine, and a pipeline that loads the plugin
through its installed entry point, as gateway apps do.
"""

from collections.abc import Iterator
from importlib.metadata import entry_points
from pathlib import Path
import re
import time
from typing import Any

from foca.plugins import (
    Backend,
    Context,
    NotApplicable,
    PipelineRejected,
    Principal,
    Rejected,
)
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
from foca.utils.http import DeadlineExceeded, RequestNotSent
from pydantic import ValidationError
import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML (a dependency of FOCA) has no stubs here

from foca_plugins.wes.routing.workflow_type import (
    WorkflowType,
    WorkflowTypeConfig,
)
from foca_plugins.wes.routing.workflow_type.service_info import (
    SupportCache,
    parse_support,
)

PLUGIN_ID = "wes.routing.workflow-type"
PATH = "/ga4gh/wes/v1/service-info"


class Clock:
    """Clock of a cache, set by the tests.

    Attributes:
        now: Current time, in seconds.
    """

    def __init__(self) -> None:
        """Start the clock at 1000 seconds."""
        self.now = 1000.0

    def __call__(self) -> float:
        """Get the current time.

        Returns:
            Seconds.
        """
        return self.now


def backend(name: str) -> Backend:
    """Build a candidate backend with a base URL without API path.

    Args:
        name: Identifier, also the first label of its host.

    Returns:
        Backend.
    """
    return make_backend(name, f"https://{name}.example.org")


def info_url(name: str) -> str:
    """Get the URL of the service info of a backend of `backend()`.

    Args:
        name: Identifier of the backend.

    Returns:
        URL.
    """
    return f"https://{name}.example.org{PATH}"


def service_info(
    types: dict[str, Any] | None = None, engines: Any = None
) -> dict[str, Any]:
    """Build a WES service info.

    Args:
        types: `workflow_type_versions`; defaults to CWL v1.0 and v1.2.
        engines: `workflow_engine_versions`, if any.

    Returns:
        Service info.
    """
    info: dict[str, Any] = {
        "id": "org.example.wes",
        "workflow_type_versions": (
            {"CWL": {"workflow_type_version": ["v1.0", "v1.2"]}}
            if types is None
            else types
        ),
    }
    if engines is not None:
        info["workflow_engine_versions"] = engines
    return info


def make_run(**fields: Any) -> dict[str, Any]:
    """Build a WES run request, new for each use.

    Args:
        fields: Fields to set; `None` removes one.

    Returns:
        Run request, by default of a CWL v1.2 workflow.
    """
    run: dict[str, Any] = {
        "workflow_url": "main.cwl",
        "workflow_type": "CWL",
        "workflow_type_version": "v1.2",
        "workflow_attachment": [],
    }
    run.update(fields)
    return {key: value for key, value in run.items() if value is not None}


def make_context(
    original: Any = None, http: FakeHttp | None = None, seconds: float = 10
) -> Context:
    """Build the context of a hook call.

    Args:
        original: Run request as submitted; defaults to `make_run()`.
        http: HTTP client; none if `None`.
        seconds: Seconds until the deadline.

    Returns:
        Context.
    """
    deadline = time.monotonic() + seconds
    return Context(
        api="wes",
        plugin_id=PLUGIN_ID,
        principal=Principal(subject="alice"),
        params=EmptyModel(),
        original=make_run() if original is None else original,
        deadline=deadline,
        state={},
        request_id="r1",
        http=None if http is None else http.client(deadline=deadline),
    )


def make_plugin(**config: Any) -> WorkflowType:
    """Create the plugin.

    Args:
        config: Admin settings.

    Returns:
        Plugin.
    """
    return WorkflowType(PLUGIN_ID, WorkflowTypeConfig(**config))


def route(
    plugin: WorkflowType, candidates: list[Backend], ctx: Context
) -> list[str] | None:
    """Route, and get the identifiers of the kept candidates.

    Args:
        plugin: Plugin.
        candidates: Candidates.
        ctx: Context.

    Returns:
        Identifiers, in order; `None` if the run was rejected (`422`).
    """
    try:
        return [b.id for b in plugin.on_route(candidates, ctx)]
    except Rejected as exc:
        assert exc.status == 422
        return None


@pytest.fixture
def http() -> FakeHttp:
    """Create a fake HTTP client with the service infos of `a` to `d`.

    `a` and `c` support CWL v1.0 and v1.2, `b` WDL 1.0 only, `d` CWL v1.0
    only.

    Returns:
        HTTP client.
    """
    http = FakeHttp()
    http.add("GET", info_url("a"), json=service_info())
    http.add(
        "GET",
        info_url("b"),
        json=service_info({"WDL": {"workflow_type_version": ["1.0"]}}),
    )
    http.add("GET", info_url("c"), json=service_info())
    http.add(
        "GET",
        info_url("d"),
        json=service_info({"CWL": {"workflow_type_version": ["v1.0"]}}),
    )
    return http


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is WorkflowType


def test_plugin_class_is_valid_for_wes_only() -> None:
    """FOCA accepts the class for WES gateways only; it routes only."""
    assert check_plugin_class(WorkflowType, PLUGIN_ID, "wes") is WorkflowType
    with pytest.raises(ValueError, match="tes"):
        check_plugin_class(WorkflowType, PLUGIN_ID, "tes")
    assert WorkflowType.phases() == ("route",)
    title, description = WorkflowType.summary()
    assert title == "Route runs to the backends that support their workflow type."
    assert description.startswith("Keeps the candidate backends")
    assert WorkflowType.data_transfers == ()
    assert WorkflowType.data_stored == ()


def test_plugin_meets_contract(http: FakeHttp) -> None:
    """The plugin passes FOCA's contract checks.

    Args:
        http: HTTP client.
    """
    check_plugin_contract(
        WorkflowType,
        api="wes",
        plugin_id=PLUGIN_ID,
        inbound=make_inbound(make_run()),
        candidates=[backend("a"), backend("b"), backend("c")],
        http=http,
    )


def test_settings_have_defaults() -> None:
    """All settings are optional; paths may have reserved characters."""
    config = WorkflowTypeConfig()
    assert config.service_info_path == PATH
    assert config.unreadable == "keep"
    assert (config.cache_ttl, config.failure_ttl, config.cache_size) == (
        300,
        30,
        1000,
    )
    path = "/a/%2Fb/service-info:x@y"
    assert WorkflowTypeConfig(service_info_path=path).service_info_path == path


@pytest.mark.parametrize(
    "data",
    [
        {"other": "value"},
        {"service_info_path": ""},
        {"service_info_path": "service-info"},
        {"service_info_path": "/"},
        {"service_info_path": "//service-info"},
        {"service_info_path": "/service-info/"},
        {"service_info_path": "/service-info?x=1"},
        {"service_info_path": "/service-info#x"},
        {"service_info_path": "/service info"},
        {"service_info_path": "/%zz"},
        {"service_info_path": "/" + "a" * 256},
        {"unreadable": "maybe"},
        {"cache_ttl": -1},
        {"failure_ttl": 86401},
        {"cache_size": -1},
    ],
)
def test_settings_are_validated(data: dict[str, Any]) -> None:
    """Invalid or unknown settings are refused.

    Args:
        data: Invalid settings.
    """
    with pytest.raises(ValidationError):
        WorkflowTypeConfig.model_validate(data)


def test_on_route_keeps_supporting_backends_in_order(http: FakeHttp) -> None:
    """Backends with the type and version stay, in order; others go.

    Args:
        http: HTTP client.
    """
    candidates = (backend("c"), backend("b"), backend("d"), backend("a"))
    ctx = make_context(http=http)
    result = make_plugin().on_route(candidates, ctx)
    assert isinstance(result, list)
    assert [b.id for b in result] == ["c", "a"]
    assert ctx.annotations == {
        "candidates": 4,
        "kept": 2,
        "unsupported": 2,
        "unreadable": 0,
        "skipped": 0,
        "fetched": 4,
        "cache_hits": 0,
    }
    assert [(call.method, call.url) for call in http.calls] == [
        ("GET", info_url(name)) for name in "cbda"
    ]


def test_types_ignore_case_versions_do_not(http: FakeHttp) -> None:
    """`cwl` matches `CWL`; `V1.2` doesn't match `v1.2`.

    Args:
        http: HTTP client.
    """
    plugin = make_plugin()
    candidates = [backend("a"), backend("d")]
    run = make_run(workflow_type="cwl", workflow_type_version="v1.0")
    assert route(plugin, candidates, make_context(run, http)) == ["a", "d"]
    run = make_run(workflow_type_version="V1.2")
    assert route(plugin, candidates, make_context(run, http)) is None


@pytest.mark.parametrize("version", [None, "", 12])
def test_runs_without_version_need_the_type(http: FakeHttp, version: Any) -> None:
    """Runs without a type version (or none that is a string) need the type.

    Args:
        http: HTTP client.
        version: Missing, empty, or no string.
    """
    run = make_run(workflow_type_version=version)
    candidates = [backend("a"), backend("b"), backend("d")]
    assert route(make_plugin(), candidates, make_context(run, http)) == ["a", "d"]


@pytest.mark.parametrize("entry", [{}, {"workflow_type_version": []}, {"x": 1}])
def test_types_without_versions_support_any(entry: dict[str, Any]) -> None:
    """A type listed without versions supports every version.

    Args:
        entry: Entry of the type, without versions.
    """
    http = FakeHttp()
    http.add("GET", info_url("a"), json=service_info({"CWL": entry}))
    assert route(make_plugin(), [backend("a")], make_context(http=http)) == ["a"]


@pytest.mark.parametrize(
    ("fields", "kept"),
    [
        ({}, None),  # CWL: versions no list
        ({"workflow_type": "WDL", "workflow_type_version": None}, None),
        ({"workflow_type": "NFL", "workflow_type_version": "DSL2"}, ["a"]),
        ({"workflow_type": "NFL", "workflow_type_version": "1"}, None),
    ],
)
def test_type_entries_of_other_shapes(
    fields: dict[str, Any], kept: list[str] | None
) -> None:
    """Entries of other shapes count as not listed; other versions as absent.

    Args:
        fields: Fields of the run.
        kept: Identifiers of the kept backends; `None` if rejected.
    """
    http = FakeHttp()
    types = {
        "CWL": {"workflow_type_version": "v1.2"},
        "WDL": ["1.0"],
        "NFL": {"workflow_type_version": [1, "DSL2"]},
    }
    http.add("GET", info_url("a"), json=service_info(types))
    ctx = make_context(make_run(**fields), http)
    assert route(make_plugin(), [backend("a")], ctx) == kept


@pytest.mark.parametrize(
    "version", ["v1.0", "v1.2", "1.1"], ids=["first", "second", "any"]
)
def test_types_differing_in_case_are_merged(version: str) -> None:
    """Entries of the same type in another case add their versions.

    Args:
        version: Version of the run's type (`1.1` for WDL, else CWL).
    """
    http = FakeHttp()
    types = {
        "CWL": {"workflow_type_version": ["v1.0"]},
        "cwl": {"workflow_type_version": ["v1.2"]},
        "WDL": {"workflow_type_version": ["1.0"]},
        "wdl": {},
    }
    http.add("GET", info_url("a"), json=service_info(types))
    type_ = "WDL" if version == "1.1" else "CWL"
    run = make_run(workflow_type=type_, workflow_type_version=version)
    assert route(make_plugin(), [backend("a")], make_context(run, http)) == ["a"]


@pytest.mark.parametrize(
    ("fields", "kept"),
    [
        ({}, ["a", "b", "c"]),
        ({"workflow_engine": "CWLTOOL"}, ["a"]),
        ({"workflow_engine": "cwltool", "workflow_engine_version": "3.1"}, ["a"]),
        ({"workflow_engine": "cwltool", "workflow_engine_version": "3.2"}, None),
        ({"workflow_engine": "toil", "workflow_engine_version": "9"}, ["c"]),
        ({"workflow_engine": "other"}, None),
        # an engine version counts only with an engine
        ({"workflow_engine_version": "0"}, ["a", "b", "c"]),
        ({"workflow_engine": "", "workflow_engine_version": "0"}, ["a", "b", "c"]),
    ],
)
def test_runs_with_engine_need_it(
    fields: dict[str, Any], kept: list[str] | None
) -> None:
    """Runs with an engine need it, and its version if they set one.

    `a` has cwltool 3.1, `b` no engines, `c` Toil in any version.

    Args:
        fields: Fields of the run.
        kept: Identifiers of the kept backends; `None` if rejected.
    """
    http = FakeHttp()
    engines = {"cwltool": {"workflow_engine_version": ["3.1"]}}
    http.add("GET", info_url("a"), json=service_info(engines=engines))
    http.add("GET", info_url("b"), json=service_info())
    http.add("GET", info_url("c"), json=service_info(engines={"Toil": {}}))
    candidates = [backend("a"), backend("b"), backend("c")]
    ctx = make_context(make_run(**fields), http)
    assert route(make_plugin(), candidates, ctx) == kept


@pytest.mark.parametrize(
    ("fields", "kept"),
    [
        ({"workflow_engine": "cwltool", "workflow_engine_version": "3.1"}, ["a"]),
        ({"workflow_engine": "cwltool"}, ["a"]),
        ({"workflow_engine": "cwltool", "workflow_engine_version": "3.2"}, None),
        ({"workflow_engine": "toil"}, None),
    ],
)
def test_engine_versions_of_wes_1_0(
    fields: dict[str, Any], kept: list[str] | None
) -> None:
    """Engines with a version string (WES 1.0) support that version only.

    Args:
        fields: Fields of the run.
        kept: Identifiers of the kept backends; `None` if rejected.
    """
    http = FakeHttp()
    engines = {"cwltool": "3.1", "toil": 5}  # no string: not listed
    http.add("GET", info_url("a"), json=service_info(engines=engines))
    ctx = make_context(make_run(**fields), http)
    assert route(make_plugin(), [backend("a")], ctx) == kept


def test_rejection_names_the_fields(http: FakeHttp) -> None:
    """Runs that no candidate supports are rejected; the detail names fields.

    Args:
        http: HTTP client.
    """
    plugin = make_plugin()
    ctx = make_context(make_run(workflow_type="SMK"), http)
    with pytest.raises(Rejected) as exc_info:
        plugin.on_route([backend("a"), backend("b")], ctx)
    assert exc_info.value.status == 422
    assert exc_info.value.detail == (
        "No backend supports the run's workflow_type, workflow_type_version"
    )
    assert ctx.annotations["unsupported"] == 2
    run = make_run(
        workflow_type="SMK",
        workflow_type_version=None,
        workflow_engine="snakemake",
        workflow_engine_version="8",
    )
    with pytest.raises(Rejected) as exc_info:
        plugin.on_route([backend("a")], make_context(run, http))
    assert exc_info.value.detail == (
        "No backend supports the run's workflow_type, workflow_engine,"
        " workflow_engine_version"
    )


def test_no_candidates_give_no_candidates(http: FakeHttp) -> None:
    """Without candidates, there is nothing to request or reject.

    Args:
        http: HTTP client.
    """
    ctx = make_context(http=http)
    assert make_plugin().on_route([], ctx) == []
    assert ctx.annotations["candidates"] == 0
    assert http.calls == []


UNREADABLE: list[dict[str, Any]] = [
    {"status": 500, "json": {"msg": "error"}},
    {"status": 401, "json": service_info()},
    {"status": 302, "headers": {"Location": "https://x.example.org/"}},
    {"content": b"<html>"},
    {"json": ["CWL"]},
    {"json": {"id": "org.example.wes"}},
    {"json": {"workflow_type_versions": None}},
    {"json": {"workflow_type_versions": ["CWL"]}},
    {"json": service_info(engines=["cwltool"])},
    {"error": RequestNotSent("refused")},
]


@pytest.mark.parametrize("response", UNREADABLE)
@pytest.mark.parametrize("unreadable", ["keep", "drop"])
def test_unreadable_service_info_is_kept_or_dropped(
    http: FakeHttp,
    response: dict[str, Any],
    unreadable: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Backends whose service info can't be read are kept or dropped.

    Args:
        http: HTTP client.
        response: Response of the backend `x`, or its error.
        unreadable: Admin setting.
        caplog: Pytest fixture.
    """
    http.add("GET", info_url("x"), **response)
    ctx = make_context(http=http)
    plugin = make_plugin(unreadable=unreadable)
    result = route(plugin, [backend("x"), backend("b"), backend("a")], ctx)
    assert result == (["x", "a"] if unreadable == "keep" else ["a"])
    assert (ctx.annotations["unreadable"], ctx.annotations["unsupported"]) == (1, 1)
    assert "Service info of backend x unreadable" in caplog.text


def test_unreadable_backends_keep_runs_from_rejection(http: FakeHttp) -> None:
    """If unreadable backends were dropped, an empty route isn't a rejection.

    One of them may support the run: the gateway answers that no backend is
    available (`503`), not that the run is unsupported.

    Args:
        http: HTTP client.
    """
    http.add("GET", info_url("x"), status=503)
    plugin = make_plugin(unreadable="drop")
    assert route(plugin, [backend("x"), backend("b")], make_context(http=http)) == []
    assert route(plugin, [backend("x")], make_context(http=http)) == []
    keep = make_plugin()
    assert route(keep, [backend("x"), backend("b")], make_context(http=http)) == ["x"]


def test_service_info_is_cached(http: FakeHttp) -> None:
    """Service infos are requested once per backend and `cache_ttl`.

    Args:
        http: HTTP client.
    """
    plugin = make_plugin(cache_ttl=60)
    clock = Clock()
    plugin.runtime.cache._clock = clock
    candidates = [backend("a"), backend("b")]
    plugin.on_route(candidates, make_context(http=http))
    ctx = make_context(http=http)
    assert route(plugin, candidates, ctx) == ["a"]
    assert (ctx.annotations["fetched"], ctx.annotations["cache_hits"]) == (0, 2)
    assert len(http.calls) == 2
    clock.now += 60
    ctx = make_context(http=http)
    plugin.on_route(candidates, ctx)
    assert (ctx.annotations["fetched"], ctx.annotations["cache_hits"]) == (2, 0)
    assert len(http.calls) == 4


def test_failures_are_cached_for_failure_ttl(http: FakeHttp) -> None:
    """Unreadable service infos are requested again after `failure_ttl`.

    Args:
        http: HTTP client.
    """
    http.add("GET", info_url("x"), status=500)
    http.add("GET", info_url("x"), json=service_info())
    plugin = make_plugin(failure_ttl=10, unreadable="drop")
    clock = Clock()
    plugin.runtime.cache._clock = clock
    assert route(plugin, [backend("x"), backend("a")], make_context(http=http)) == ["a"]
    ctx = make_context(http=http)
    assert route(plugin, [backend("x"), backend("a")], ctx) == ["a"]
    assert (ctx.annotations["cache_hits"], ctx.annotations["unreadable"]) == (2, 1)
    clock.now += 10
    ctx = make_context(http=http)
    assert route(plugin, [backend("x"), backend("a")], ctx) == ["x", "a"]
    assert ctx.annotations["fetched"] == 1


@pytest.mark.parametrize(
    "config", [{"cache_size": 0}, {"cache_ttl": 0, "failure_ttl": 0}]
)
def test_cache_can_be_turned_off(http: FakeHttp, config: dict[str, Any]) -> None:
    """With size or TTLs 0, every call requests the service infos.

    Args:
        http: HTTP client.
        config: Admin settings.
    """
    http.add("GET", info_url("x"), status=500)
    plugin = make_plugin(**config)
    for _ in range(2):
        plugin.on_route([backend("a"), backend("x")], make_context(http=http))
    assert len(http.calls) == 4


def test_cache_drops_least_recently_used() -> None:
    """Beyond its size, the cache drops the entry used least recently."""
    cache = SupportCache(2)
    cache.put("a", None, 10)
    cache.put("b", None, 10)
    assert cache.get("a") == (True, None)
    cache.put("c", None, 10)
    assert cache.get("b") == (False, None)
    assert cache.get("a") == (True, None)
    assert cache.get("c") == (True, None)


def test_parse_support_skips_names_that_are_no_strings() -> None:
    """Names that aren't strings (impossible in JSON) count as not listed."""
    support = parse_support({"workflow_type_versions": {1: {}, "CWL": {}}})
    assert support.types == {"cwl": None}
    assert support.engines == {}


def test_base_urls_with_trailing_slash(http: FakeHttp) -> None:
    """The path is appended to base URLs without doubling slashes.

    Args:
        http: HTTP client.
    """
    candidate = make_backend("a", "https://a.example.org/")
    assert make_plugin().on_route([candidate], make_context(http=http)) == [candidate]
    assert [call.url for call in http.calls] == [info_url("a")]


def test_service_info_path_is_configurable() -> None:
    """Backends whose API is below another path are supported."""
    http = FakeHttp()
    http.add("GET", "https://a.example.org/wes/service-info", json=service_info())
    plugin = make_plugin(service_info_path="/wes/service-info")
    assert route(plugin, [backend("a")], make_context(http=http)) == ["a"]


def test_no_time_left_skips_backends_uncached(http: FakeHttp) -> None:
    """Without time left, backends count as unreadable, but aren't cached.

    Args:
        http: HTTP client.
    """
    plugin = make_plugin(unreadable="drop")
    ctx = make_context(http=http, seconds=-1)
    assert route(plugin, [backend("a"), backend("b")], ctx) == []
    assert http.calls == []
    assert (ctx.annotations["skipped"], ctx.annotations["unreadable"]) == (2, 2)
    http.add("GET", info_url("x"), error=DeadlineExceeded("late"))
    ctx = make_context(http=http)
    assert route(plugin, [backend("x"), backend("a")], ctx) == ["a"]
    assert (ctx.annotations["skipped"], ctx.annotations["fetched"]) == (1, 2)
    assert plugin.runtime.cache.get(info_url("x")) == (False, None)
    assert plugin.runtime.cache.get(info_url("a"))[0] is True


@pytest.mark.parametrize(
    "original",
    [
        "not a run",
        {"workflow_url": "main.cwl"},
        make_run(workflow_type=""),
        make_run(workflow_type=["CWL"]),
    ],
)
def test_not_applicable_without_workflow_type(http: FakeHttp, original: Any) -> None:
    """Requests that are no run requests with a workflow type are skipped.

    Args:
        http: HTTP client.
        original: Request payload.
    """
    with pytest.raises(NotApplicable):
        make_plugin().on_route([backend("a")], make_context(original, http))
    assert http.calls == []


def test_needs_http_client() -> None:
    """Contexts built without HTTP client are a programming error."""
    with pytest.raises(RuntimeError, match="HTTP client"):
        make_plugin().on_route([backend("a")], make_context())


def test_setup_replaces_settings_and_cache(http: FakeHttp) -> None:
    """`setup()` replaces the settings and empties the cache.

    Args:
        http: HTTP client.
    """
    plugin = make_plugin()
    plugin.on_route([backend("a")], make_context(http=http))
    assert plugin.runtime.cache.get(info_url("a"))[0] is True
    plugin.setup(WorkflowTypeConfig(unreadable="drop"))
    assert plugin.runtime.settings.unreadable == "drop"
    assert plugin.runtime.cache.get(info_url("a")) == (False, None)


def make_harness(http: FakeHttp) -> PluginHarness:
    """Create a harness of the plugin in a WES gateway.

    Args:
        http: HTTP client.

    Returns:
        Harness.
    """
    return PluginHarness(WorkflowType, plugin_id=PLUGIN_ID, api="wes", http=http)


def test_harness_routes_and_rejects(http: FakeHttp) -> None:
    """Through the engine, routes keep supporting backends; rejections stop.

    Args:
        http: HTTP client.
    """
    candidates = [backend("b"), backend("a"), backend("c")]
    with make_harness(http) as harness:
        routed = harness.run_route(candidates, make_inbound(make_run()))
        assert [b.id for b in routed] == ["a", "c"]
        (execution,) = harness.executions
        assert (execution.phase, execution.outcome) == ("route", "ok")
        assert execution.annotations["kept"] == 2
    with make_harness(http) as harness, pytest.raises(PipelineRejected):
        harness.run_route(candidates, make_inbound(make_run(workflow_type="SMK")))


@pytest.fixture
def host(http: FakeHttp) -> Iterator[PluginHost]:
    """Create a plugin host of a WES gateway that loads the installed plugin.

    Args:
        http: HTTP client of the calls.

    Yields:
        Host whose default pipeline routes with the plugin.
    """
    conf = PluginsConfig.model_validate(
        {
            "available": {PLUGIN_ID: {"config": {"unreadable": "drop"}}},
            "pipeline": [
                {"id": "workflow-type", "alternatives": [{"plugin": PLUGIN_ID}]}
            ],
        }
    )
    host = PluginHost(conf, api="wes", http_client=http.client)
    yield host
    host.close()


def test_pipeline_runs_plugin(host: PluginHost, http: FakeHttp) -> None:
    """The engine loads the installed plugin and routes with it.

    Args:
        host: Plugin host.
        http: HTTP client of the calls.
    """
    run = host.start(
        make_inbound(make_run()), Principal(subject="alice"), request_id="r1"
    )
    run.request()
    routed = run.route([backend("d"), backend("c"), backend("x"), backend("a")])
    assert [b.id for b in routed] == ["c", "a"]
    assert run.provenance()["route"]["candidates_out"] == ["c", "a"]
    assert info_url("x") in [call.url for call in http.calls]


def test_readme_configuration_is_valid(http: FakeHttp) -> None:
    """The configuration in the README loads, and its pipeline routes.

    Args:
        http: HTTP client of the calls.
    """
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    match = re.search(r"## Configuration\n\n```yaml\n(.*?)```", readme, re.DOTALL)
    assert match is not None
    conf = PluginsConfig.model_validate(yaml.safe_load(match.group(1))["plugins"])
    host = PluginHost(conf, api="wes", http_client=http.client)
    try:
        run = host.start(make_inbound(make_run()), Principal(subject="alice"))
        run.request()
        routed = run.route([backend("b"), backend("a")])
        assert [b.id for b in routed] == ["a"]
        assert [e.phase for e in run.executions] == ["route", "route"]
    finally:
        host.close()
