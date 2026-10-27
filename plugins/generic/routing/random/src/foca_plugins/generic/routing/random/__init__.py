"""Plugin `generic.routing.random`: route to the candidates in random order.

The successor of proTES's random task distribution
(`TaskDistributionRandom`): the route step returns all candidate backends,
shuffled, so that requests spread over them without round-robin state.
Gateways then try the candidates in that order (cf. FOCA's forwarding).

FOCA's default router `foca.routing.ordered` keeps the order of the
backend list and shuffles only the backends of each service registry among
themselves; this plugin shuffles all candidates, configured or discovered.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from collections.abc import Sequence
import random

from foca.plugins import Backend, Context, InvalidParams, Plugin
from pydantic import BaseModel, ConfigDict, StrictInt


class RandomConfig(BaseModel):
    """Admin settings (`plugins.available."generic.routing.random".config`).

    Attributes:
        allow_seed: Whether pipelines may set a `seed` (for tests only:
            seeded orders are predictable, also for clients that choose the
            plugin in their own pipelines).
    """

    model_config = ConfigDict(extra="forbid")

    allow_seed: bool = False


class RandomParams(BaseModel):
    """Parameters of the plugin in a pipeline step.

    Attributes:
        seed: Seed of the shuffling, for reproducible orders in tests;
            an integer (no booleans, floats or strings); refused unless
            `allow_seed` is set. Without a seed, the order
            comes from the operating system's randomness
            (`random.SystemRandom`).
    """

    model_config = ConfigDict(extra="forbid")

    seed: StrictInt | None = None


class Random(Plugin[RandomConfig, RandomParams]):
    """Route to the candidates in random order.

    Attributes:
        allow_seed: Whether pipelines may set a `seed`
            (`RandomConfig.allow_seed`).
    """

    Config = RandomConfig
    Params = RandomParams
    apis = frozenset({"*"})
    maturity = "experimental"
    description = (
        "Returns all candidate backends in random order, so that requests"
        " spread over them; with a seed (if the admin allows it, for tests),"
        " the order depends only on the seed and the order of the candidates"
        " (on the same Python version)."
    )

    # Set by `setup()`, from the admin settings
    allow_seed: bool

    def setup(self, config: RandomConfig) -> None:
        """Keep the admin settings; idempotent, as it may run more than once.

        Args:
            config: Validated admin settings.
        """
        self.allow_seed = config.allow_seed

    def check_params(self, params: RandomParams) -> None:
        """Refuse seeds unless the admin settings allow them.

        Args:
            params: Validated parameters of a pipeline step.

        Raises:
            InvalidParams: A seed is set, but not allowed.
        """
        if params.seed is not None and not self.allow_seed:
            raise InvalidParams("'seed' is not allowed (cf. 'allow_seed')")

    def on_route(self, candidates: Sequence[Backend], ctx: Context) -> list[Backend]:
        """Shuffle the candidate backends.

        Args:
            candidates: Candidate backends, in order.
            ctx: Context of the call.

        Returns:
            All candidates, in random order (a new list).
        """
        seed = _params(ctx).seed
        shuffled = list(candidates)
        _rng(seed).shuffle(shuffled)
        ctx.record("candidates", len(shuffled))
        ctx.record("seeded", seed is not None)
        return shuffled


def _rng(seed: int | None) -> random.Random:
    """Create the random number generator of a call.

    Args:
        seed: Seed, or `None` for the operating system's randomness.

    Returns:
        `random.SystemRandom` without a seed, else `random.Random(seed)`.
    """
    if seed is None:
        return random.SystemRandom()
    return random.Random(seed)  # noqa: S311 (seeded for tests only)


def _params(ctx: Context) -> RandomParams:
    """Get the parameters of the call, with their type.

    Args:
        ctx: Context of the call; the engine has validated its parameters
            with `RandomParams`.

    Returns:
        Parameters.

    Raises:
        TypeError: The parameters are of another model.
    """
    if not isinstance(ctx.params, RandomParams):
        raise TypeError(f"Unexpected parameters: {type(ctx.params).__name__}")
    return ctx.params
