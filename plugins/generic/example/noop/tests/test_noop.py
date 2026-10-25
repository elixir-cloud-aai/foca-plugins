"""Tests of the example plugin `generic.example.noop`.

Unit tests call the hooks with a context of their own; the pipeline tests
load the plugin through its installed entry point, as gateway apps do, and
run it with FOCA's plugin engine. Copies of the template keep both kinds.
"""

from collections.abc import Iterator
from importlib.metadata import entry_points
import time
from typing import Any

from foca.plugins import Backend, Context, InboundRequest, Principal
from foca.plugins.base import check_plugin_class
from foca.plugins.config import PluginsConfig
from foca.plugins.engine import PluginHost
from pydantic import BaseModel, ValidationError
import pytest

from foca_plugins.generic.example.noop import Noop, NoopConfig, NoopParams

PLUGIN_ID = "generic.example.noop"
BACKENDS = [
    Backend(id="a", url="https://a.example.org"),
    Backend(id="b", url="https://b.example.org"),
]


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


def make_inbound() -> InboundRequest:
    """Build an inbound request.

    Returns:
        Request with a task as its payload.
    """
    return InboundRequest(
        method="POST", path="/ga4gh/tes/v1/tasks", payload={"name": "task"}
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
            "available": {PLUGIN_ID: {"config": {"label": "gateway-1"}}},
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
    request = make_inbound()
    ctx = make_context()
    plugin.on_request(request, ctx)
    assert request == make_inbound()
    assert ctx.annotations == {"label": "gateway-1"}


def test_on_route_keeps_candidates(plugin: Noop) -> None:
    """All candidates are kept, in order, in a new list.

    Args:
        plugin: Plugin.
    """
    ctx = make_context()
    candidates = tuple(BACKENDS)
    result = plugin.on_route(candidates, ctx)
    assert result == BACKENDS
    assert ctx.annotations == {"candidates": 2}


def test_hooks_record_nothing_if_disabled(plugin: Noop) -> None:
    """With `record: false`, no annotations are recorded.

    Args:
        plugin: Plugin.
    """
    ctx = make_context(NoopParams(record=False))
    plugin.on_request(make_inbound(), ctx)
    assert plugin.on_route(BACKENDS, ctx) == BACKENDS
    assert ctx.annotations == {}


def test_hooks_refuse_other_params(plugin: Noop) -> None:
    """Parameters of another model are a programming error.

    Args:
        plugin: Plugin.
    """
    with pytest.raises(TypeError, match="Unexpected parameters"):
        plugin.on_request(make_inbound(), make_context(NoopConfig()))


def test_pipeline_runs_plugin(host: PluginHost) -> None:
    """The engine loads the installed plugin and runs both hooks.

    Args:
        host: Plugin host.
    """
    run = host.start(make_inbound(), Principal(subject="alice"), request_id="r1")
    assert run.request().payload == {"name": "task"}
    assert run.route(BACKENDS) == BACKENDS
    assert [(e.phase, e.outcome, e.annotations) for e in run.executions] == [
        ("request", "ok", {"label": "gateway-1"}),
        ("route", "ok", {"candidates": 2}),
    ]
