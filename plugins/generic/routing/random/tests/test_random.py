"""Tests of the plugin `generic.routing.random`.

Unit tests call the hook with a context of their own; the other tests use
FOCA's plugin test kit (`foca.plugins.testing`): the contract checks, a
harness that runs the plugin through the real engine, and a pipeline that
loads the plugin through its installed entry point, as gateway apps do.
"""

from collections.abc import Iterator
from importlib.metadata import entry_points
import random
import time
from typing import Any

from foca.plugins import Context, InvalidParams, Principal
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

from foca_plugins.generic.routing.random import Random, RandomConfig, RandomParams

PLUGIN_ID = "generic.routing.random"
BACKENDS = [make_backend(name) for name in "abcdefgh"]


def make_task() -> dict[str, Any]:
    """Build a task, new for each use, so that no test shares a payload.

    Returns:
        Task.
    """
    return {"name": "task"}


def make_context(params: BaseModel | None = None) -> Context:
    """Build the context of a hook call.

    Args:
        params: Parameters; defaults to those of `RandomParams()`.

    Returns:
        Context.
    """
    return Context(
        api="tes",
        plugin_id=PLUGIN_ID,
        principal=Principal(subject="alice"),
        params=RandomParams() if params is None else params,
        original=make_task(),
        deadline=time.monotonic() + 10,
        state={},
        request_id="r1",
    )


def make_host(config: dict[str, Any], params: dict[str, Any]) -> PluginHost:
    """Create a plugin host that loads the installed plugin.

    Args:
        config: Admin settings of the plugin.
        params: Parameters of the plugin in the route step.

    Returns:
        Host whose default pipeline routes with the plugin.
    """
    conf = PluginsConfig.model_validate(
        {
            "available": {PLUGIN_ID: {"config": config}},
            "pipeline": [
                {
                    "id": "route",
                    "alternatives": [{"plugin": PLUGIN_ID, "params": params}],
                }
            ],
        }
    )
    return PluginHost(conf, api="tes")


@pytest.fixture
def plugin() -> Random:
    """Create the plugin, with seeds allowed.

    Returns:
        Plugin.
    """
    return Random(PLUGIN_ID, RandomConfig(allow_seed=True))


@pytest.fixture
def host() -> Iterator[PluginHost]:
    """Create a plugin host that loads the installed plugin, without seed.

    Yields:
        Host.
    """
    host = make_host({}, {})
    yield host
    host.close()


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is Random


@pytest.mark.parametrize("api", ["tes", "wes"])
def test_plugin_class_is_valid(api: str) -> None:
    """FOCA accepts the class for every API.

    Args:
        api: API of a gateway app.
    """
    assert check_plugin_class(Random, PLUGIN_ID, api) is Random


def test_plugin_class_describes_itself() -> None:
    """The class routes only, describes itself and sends no data out."""
    assert Random.phases() == ("route",)
    title, description = Random.summary()
    assert title == "Route to the candidates in random order."
    assert description.startswith("Returns all candidate backends")
    assert Random.data_transfers == ()


def test_plugin_meets_contract() -> None:
    """The plugin passes FOCA's contract checks, also with a seed."""
    check_plugin_contract(Random, plugin_id=PLUGIN_ID, candidates=BACKENDS)
    check_plugin_contract(
        Random,
        config={"allow_seed": True},
        params={"seed": 1},
        plugin_id=PLUGIN_ID,
        candidates=BACKENDS,
    )


def test_settings_have_defaults() -> None:
    """Seeds are refused by default; parameters are optional."""
    assert Random(PLUGIN_ID, RandomConfig()).allow_seed is False
    assert RandomParams().seed is None


def test_seed_zero_is_a_seed(plugin: Random) -> None:
    """`seed: 0` seeds the order (it is not taken for "no seed").

    Args:
        plugin: Plugin.
    """
    ctx = make_context(RandomParams(seed=0))
    expected = list(BACKENDS)
    random.Random(0).shuffle(expected)  # noqa: S311 (test of a seeded order)
    assert plugin.on_route(BACKENDS, ctx) == expected
    assert ctx.annotations == {"candidates": 8, "seeded": True}
    with pytest.raises(InvalidParams):
        Random(PLUGIN_ID, RandomConfig()).check_params(RandomParams(seed=0))


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (RandomConfig, {"other": "value"}),
        (RandomConfig, {"allow_seed": "maybe"}),
        (RandomParams, {"other": "value"}),
        (RandomParams, {"seed": "abc"}),
        (RandomParams, {"seed": "7"}),
        (RandomParams, {"seed": 1.0}),
        (RandomParams, {"seed": True}),
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


def test_check_params_refuses_seed_unless_allowed() -> None:
    """Seeds need `allow_seed`; no seed is always fine."""
    strict = Random(PLUGIN_ID, RandomConfig())
    with pytest.raises(InvalidParams, match="allow_seed"):
        strict.check_params(RandomParams(seed=1))
    strict.check_params(RandomParams())
    Random(PLUGIN_ID, RandomConfig(allow_seed=True)).check_params(RandomParams(seed=1))


def test_on_route_returns_permutation(plugin: Random) -> None:
    """All candidates are kept, each once, in a new list; inputs unchanged.

    Args:
        plugin: Plugin.
    """
    candidates = tuple(BACKENDS)
    ctx = make_context()
    result = plugin.on_route(candidates, ctx)
    assert sorted(result, key=lambda b: b.id) == BACKENDS
    assert candidates == tuple(BACKENDS)
    assert ctx.annotations == {"candidates": 8, "seeded": False}


def test_on_route_uses_system_randomness(
    plugin: Random, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a seed, the order comes from `random.SystemRandom`.

    Args:
        plugin: Plugin.
        monkeypatch: Pytest fixture.
    """
    used: list[str] = []

    def reverse(self: random.Random, items: list[Any]) -> None:
        used.append(type(self).__name__)
        items.reverse()

    monkeypatch.setattr(random.SystemRandom, "shuffle", reverse)
    assert plugin.on_route(BACKENDS, make_context()) == BACKENDS[::-1]
    assert used == ["SystemRandom"]


def test_on_route_varies_without_seed(plugin: Random) -> None:
    """Unseeded orders differ between calls (with near certainty).

    Args:
        plugin: Plugin.
    """
    orders = {
        tuple(b.id for b in plugin.on_route(BACKENDS, make_context()))
        for _ in range(20)
    }
    # 8! orders: 20 equal draws have a probability of about 1e-88
    assert len(orders) > 1


def test_on_route_is_reproducible_with_seed(plugin: Random) -> None:
    """A seed gives the same order for the same candidates, every time.

    Args:
        plugin: Plugin.
    """
    params = RandomParams(seed=42)
    ctx = make_context(params)
    first = plugin.on_route(BACKENDS, ctx)
    assert plugin.on_route(BACKENDS, make_context(params)) == first
    expected = list(BACKENDS)
    random.Random(42).shuffle(expected)  # noqa: S311 (test of a seeded order)
    assert first == expected
    assert first != BACKENDS
    assert ctx.annotations == {"candidates": 8, "seeded": True}


def test_on_route_keeps_empty_and_single_lists(plugin: Random) -> None:
    """No candidate or one candidate: nothing to shuffle.

    Args:
        plugin: Plugin.
    """
    assert plugin.on_route([], make_context()) == []
    assert plugin.on_route(BACKENDS[:1], make_context()) == BACKENDS[:1]


def test_on_route_refuses_other_params(plugin: Random) -> None:
    """Parameters of another model are a programming error.

    Args:
        plugin: Plugin.
    """
    with pytest.raises(TypeError, match="Unexpected parameters"):
        plugin.on_route(BACKENDS, make_context(RandomConfig()))


def test_harness_routes_with_seed() -> None:
    """Through the engine, a seeded route is reproducible and recorded."""
    harness = PluginHarness(
        Random, config={"allow_seed": True}, params={"seed": 7}, plugin_id=PLUGIN_ID
    )
    backends = [make_backend(f"h{i}") for i in range(5)]
    first = harness.run_route(backends, make_inbound(make_task()))
    assert harness.run_route(backends, make_inbound(make_task())) == first
    assert sorted(first, key=lambda b: b.id) == backends
    assert [(e.phase, e.outcome, e.annotations) for e in harness.executions] == [
        ("route", "ok", {"candidates": 5, "seeded": True}),
    ]


def test_pipeline_runs_plugin(host: PluginHost) -> None:
    """The engine loads the installed plugin and routes with it.

    Args:
        host: Plugin host.
    """
    run = host.start(
        make_inbound(make_task()), Principal(subject="alice"), request_id="r1"
    )
    run.request()
    routed = run.route(BACKENDS)
    assert sorted(routed, key=lambda b: b.id) == BACKENDS
    assert [(e.phase, e.outcome) for e in run.executions] == [("route", "ok")]
    route = run.provenance()["route"]
    assert route["candidates_out"] == [b.id for b in routed]


def test_pipeline_refuses_seed_unless_allowed() -> None:
    """A default pipeline with a seed but no `allow_seed` fails to resolve."""
    with pytest.raises(ValueError, match="allow_seed"):
        make_host({}, {"seed": 1}).close()
