"""Example plugin `generic.example.noop`: changes nothing.

It shows the conventions of the plugins of this repository and is the
template that `scripts/new_plugin.py` copies: settings models, metadata,
hooks of two phases, annotations, and no imports of a web framework. Its
hooks leave requests and backends as they are and record what they saw, so
that it can run in any pipeline of any gateway app, e.g., to try out a
configuration.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from collections.abc import Sequence

from foca.plugins import Backend, Context, InboundRequest, Plugin
from pydantic import BaseModel, ConfigDict, Field


class NoopConfig(BaseModel):
    """Admin settings (`plugins.available."generic.example.noop".config`).

    Attributes:
        label: Value of the annotation `label` of every call, e.g., to tell
            apart several gateways in the provenance records.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="noop", min_length=1, max_length=64)


class NoopParams(BaseModel):
    """Parameters of the plugin in a pipeline step.

    Attributes:
        record: Whether to record annotations.
    """

    model_config = ConfigDict(extra="forbid")

    record: bool = True


class Noop(Plugin[NoopConfig, NoopParams]):
    """Change nothing.

    The first line of the docstring is the plugin's title for clients; its
    description is set explicitly, so that it doesn't include the sections
    below (FOCA uses the rest of the docstring otherwise).

    Attributes:
        label: Value of the annotation `label` (`NoopConfig.label`).
    """

    Config = NoopConfig
    Params = NoopParams
    apis = frozenset({"*"})
    maturity = "experimental"
    description = (
        "Leaves inbound requests and the candidate backends as they are;"
        " records the label of its settings and the number of candidates, if"
        " its parameters say so."
    )

    # Set by `setup()`, from the admin settings
    label: str

    def setup(self, config: NoopConfig) -> None:
        """Keep the label; idempotent, as `setup()` may run more than once.

        Args:
            config: Validated admin settings.
        """
        self.label = config.label

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Leave the inbound request as it is.

        Args:
            request: Inbound request (a copy, which the engine keeps).
            ctx: Context of the call.
        """
        if _params(ctx).record:
            ctx.record("label", self.label)

    def on_route(self, candidates: Sequence[Backend], ctx: Context) -> list[Backend]:
        """Keep all candidate backends, in their order.

        Args:
            candidates: Candidate backends, in order.
            ctx: Context of the call.

        Returns:
            The candidates.
        """
        if _params(ctx).record:
            ctx.record("candidates", len(candidates))
        return list(candidates)


def _params(ctx: Context) -> NoopParams:
    """Get the parameters of the call, with their type.

    Args:
        ctx: Context of the call; the engine has validated its parameters
            with `NoopParams`.

    Returns:
        Parameters.

    Raises:
        TypeError: The parameters are of another model.
    """
    if not isinstance(ctx.params, NoopParams):
        raise TypeError(f"Unexpected parameters: {type(ctx.params).__name__}")
    return ctx.params
