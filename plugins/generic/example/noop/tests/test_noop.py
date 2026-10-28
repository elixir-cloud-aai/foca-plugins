"""Tests of the example plugin `generic.example.noop`.

Unit tests call the hooks with a context of their own; the other tests use
FOCA's plugin test kit (`foca.plugins.testing`): the contract checks, a
harness that runs the plugin through the real engine, and a pipeline that
loads the plugin through its installed entry point, as gateway apps do.
Copies of the template keep all of them, the contract test in particular.
"""

from collections.abc import Iterator
from importlib.metadata import entry_points
import time
from typing import Any

from foca.plugins import Backend, Context, Principal
from foca.plugins.base import check_plugin_class
from foca.plugins.config import PluginsConfig
from foca.plugins.engine import PluginHost
from foca.plugins.testing import (
    PluginHarness,
    check_plugin_contract,
    make_backend,
    make_inbound,
)
from pydantic import BaseModel, ValidationError
import pytest

from foca_plugins.generic.example.noop import Noop, NoopConfig, NoopParams

PLUGIN_ID = "generic.example.noop"
CONFIG = {"label": "gateway-1"}


def make_backends(*ids: str) -> list[Backend]:
    """Build candidate backends, new for each use (cf. `make_task()`).

    Args:
        ids: Identifiers; default to `a` and `b`.

    Returns:
        Backends.
    """
    return [make_backend(id_) for id_ in ids or ("a", "b")]


def make_task(name: str = "task") -> dict[str, Any]:
    """Build a task, new for each use, so that tests share no payload.

    A hook that changed a shared payload in place would change the expected
    value, too, and comparisons would pass however the hook behaved. Tests
    that check that hooks keep their input pass values of their own, so
    that hooks returning constants can't pass them either.

    Args:
        name: Name of the task.

    Returns:
        Task.
    """
    return {"name": name}


def make_context(params: BaseModel | None = None) -> Context:
    """Build the context of a hook call.

    Args:
        params: Parameters; defaults to those of `NoopParams()`.

    Returns:
        Context.
    """
    return Context(
        api="tes",
        plugin_id=PLUGIN_ID,
        principal=Principal(subject="alice"),
        params=NoopParams() if params is None else params,
        original={"name": "task"},
        deadline=time.monotonic() + 10,
        state={},
        request_id="r1",
    )


@pytest.fixture
def plugin() -> Noop:
    """Create the plugin with a label.

    Returns:
        Plugin.
    """
    return Noop(PLUGIN_ID, NoopConfig(label="gateway-1"))


@pytest.fixture
def host() -> Iterator[PluginHost]:
    """Create a plugin host that loads the installed plugin.

    Yields:
        Host whose default pipeline runs the plugin in its `request` and
        `route` phases.
    """
    conf = PluginsConfig.model_validate(
        {
            "available": {PLUGIN_ID: {"config": CONFIG}},
            "pipeline": [{"id": "noop", "alternatives": [{"plugin": PLUGIN_ID}]}],
        }
    )
    host = PluginHost(conf, api="tes")
    yield host
    host.close()


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is Noop


@pytest.mark.parametrize("api", ["tes", "wes"])
def test_plugin_class_is_valid(api: str) -> None:
    """FOCA accepts the class for every API.

    Args:
        api: API of a gateway app.
    """
    assert check_plugin_class(Noop, PLUGIN_ID, api) is Noop
    assert Noop.phases() == ("request", "route")
    title, description = Noop.summary()
    assert title == "Change nothing."
    assert description.startswith("Leaves inbound requests")


def test_settings_have_defaults() -> None:
    """Admin settings and parameters are optional."""
    assert Noop(PLUGIN_ID, NoopConfig()).label == "noop"
    assert NoopParams().record is True


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (NoopConfig, {"label": ""}),
        (NoopConfig, {"label": "x" * 65}),
        (NoopConfig, {"other": "value"}),
        (NoopParams, {"other": "value"}),
    ],
)
def test_settings_are_validated(model: type[BaseModel], data: dict[str, Any]) -> None:
    """Invalid or unknown settings are refused.

    Args:
        model: Settings model.
        data: Invalid settings.
    """
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_on_request_keeps_request(plugin: Noop) -> None:
    """The request is unchanged; its label is recorded.

    Args:
        plugin: Plugin.
    """
    request = make_inbound(make_task("kept"))
    ctx = make_context()
    plugin.on_request(request, ctx)
    assert request == make_inbound(make_task("kept"))
    assert ctx.annotations == {"label": "gateway-1"}


def test_on_route_keeps_candidates(plugin: Noop) -> None:
    """All candidates are kept, in order, in a new list.

    Args:
        plugin: Plugin.
    """
    ctx = make_context()
    candidates = tuple(make_backends("x", "y", "z"))
    result = plugin.on_route(candidates, ctx)
    assert result == make_backends("x", "y", "z")
    assert ctx.annotations == {"candidates": 3}


def test_hooks_record_nothing_if_disabled(plugin: Noop) -> None:
    """With `record: false`, no annotations are recorded.

    Args:
        plugin: Plugin.
    """
    ctx = make_context(NoopParams(record=False))
    plugin.on_request(make_inbound(make_task()), ctx)
    assert plugin.on_route(make_backends(), ctx) == make_backends()
    assert ctx.annotations == {}


def test_hooks_refuse_other_params(plugin: Noop) -> None:
    """Parameters of another model are a programming error.

    Args:
        plugin: Plugin.
    """
    with pytest.raises(TypeError, match="Unexpected parameters"):
        plugin.on_request(make_inbound(make_task()), make_context(NoopConfig()))


def test_plugin_meets_contract() -> None:
    """The plugin passes FOCA's contract checks (class, metadata, hooks)."""
    check_plugin_contract(Noop, config=CONFIG, plugin_id=PLUGIN_ID)


def test_harness_runs_hooks() -> None:
    """Through the engine, the hooks keep their inputs and record.

    Shows FOCA's `PluginHarness`, which runs the plugin class directly (no
    entry point); plugins that send requests pass it a `FakeHttp`.
    """
    harness = PluginHarness(
        Noop, config=CONFIG, params={"record": True}, plugin_id=PLUGIN_ID
    )
    routed = harness.run_route(make_backends("x", "y"), make_inbound(make_task()))
    assert routed == make_backends("x", "y")
    assert [(e.phase, e.outcome, e.annotations) for e in harness.executions] == [
        ("request", "ok", {"label": "gateway-1"}),
        ("route", "ok", {"candidates": 2}),
    ]


def test_pipeline_runs_plugin(host: PluginHost) -> None:
    """The engine loads the installed plugin and runs both hooks.

    Args:
        host: Plugin host.
    """
    run = host.start(
        make_inbound(make_task("kept")), Principal(subject="alice"), request_id="r1"
    )
    assert run.request().payload == make_task("kept")
    assert run.route(make_backends("x", "y")) == make_backends("x", "y")
    assert [(e.phase, e.outcome, e.annotations) for e in run.executions] == [
        ("request", "ok", {"label": "gateway-1"}),
        ("route", "ok", {"candidates": 2}),
    ]
