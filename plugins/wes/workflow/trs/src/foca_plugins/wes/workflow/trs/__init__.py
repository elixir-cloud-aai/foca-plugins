"""Plugin `wes.workflow.trs`: run WES workflows from a GA4GH TRS.

Clients of WES gateways pass a TRS URI (`trs://<host>/<tool id>/<version
id>`) or the URL of a TRS tool version as `workflow_url`. WES doesn't define
these, so most WES backends can't fetch them. In the `request` phase, this
plugin fetches the primary descriptor and the secondary descriptors of the
version, for the run's `workflow_type`, attaches them to the run
(`workflow_attachment`) and sets `workflow_url` to the primary
descriptor's relative path, as WES defines for attached workflows: every
backend then gets the same, self-contained run request.

Requests go through the gateway's guarded HTTP client (`ctx.http`: public
addresses and `https` only, no redirects, bounded response sizes, within
the step's deadline), to allowed TRS hosts only.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from dataclasses import dataclass

from foca.plugins import Context, InboundRequest, NotApplicable, Plugin, Rejected
from foca.plugins.base import EmptyModel
from pydantic import BaseModel, ConfigDict, Field

from foca_plugins.wes.workflow.trs.attach import (
    WORKFLOW_URL,
    FileLimits,
    Host,
    attach_workflow,
    check_host,
)
from foca_plugins.wes.workflow.trs.trs import (
    DESCRIPTOR_TYPES,
    InvalidTrsUri,
    TrsError,
    descriptor_type,
    fetch_descriptors,
    parse_tool_version,
)

__all__ = ["Trs", "TrsConfig", "TrsError"]


class TrsConfig(BaseModel):
    """Admin settings (`plugins.available."wes.workflow.trs".config`).

    Attributes:
        allowed_hosts: Hosts of the TRSs that workflows may come from, e.g.,
            `trs.example.org`, or with a port, `trs.example.org:8443`
            (no IPv6 addresses); none by default, so that the plugin
            rejects every TRS URI until the admin allows hosts.
        max_files: Maximum number of descriptor files of a workflow.
        max_size: Maximum total size of the descriptor files of a workflow,
            in bytes (keep it, with the files that clients attach, within
            the gateway's `plugins.attachments.max_size`).
    """

    model_config = ConfigDict(extra="forbid")

    allowed_hosts: list[Host] = Field(default_factory=list, max_length=256)
    max_files: int = Field(default=100, ge=1, le=10_000)
    max_size: int = Field(default=10 * 1024 * 1024, ge=1)


@dataclass(frozen=True)
class Settings:
    """Settings of the plugin, from its admin settings (`TrsConfig`).

    Attributes:
        allowed_hosts: Allowed TRS hosts, normalized.
        max_files: Maximum number of descriptor files.
        max_size: Maximum total size of the descriptor files, in bytes.
    """

    allowed_hosts: frozenset[str]
    max_files: int
    max_size: int


class Trs(Plugin[TrsConfig, EmptyModel]):
    """Run workflows from a GA4GH TRS.

    Attributes:
        settings: Settings, from `TrsConfig`.
    """

    Config = TrsConfig
    Params = EmptyModel
    apis = frozenset({"wes"})
    capabilities = frozenset({"trs-workflows"})
    maturity = "experimental"
    description = (
        "Runs workflows registered in a GA4GH TRS: for a 'workflow_url' that"
        " is a TRS URI (trs://<host>/<tool>/<version>) or the URL of a TRS"
        " tool version, fetches the primary and secondary descriptors of the"
        " run's 'workflow_type' from an allowed TRS host, attaches them to the"
        " run and sets 'workflow_url' to the primary descriptor. Does not"
        " apply to other runs."
    )
    data_transfers = (
        "The TRSs at the admin's 'allowed_hosts': the tool and version"
        " identifiers of TRS URIs in 'workflow_url', and the descriptor type",
    )

    # Set by `setup()`, from the admin settings
    settings: Settings

    def setup(self, config: TrsConfig) -> None:
        """Keep the settings; idempotent, as `setup()` may run more than once.

        The settings are replaced as a whole, so that calls never see a mix
        of two configurations.

        Args:
            config: Validated admin settings.
        """
        self.settings = Settings(
            frozenset(config.allowed_hosts), config.max_files, config.max_size
        )

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Fetch the workflow of a TRS URI and attach it to the run.

        Args:
            request: Inbound request (a copy, which the engine keeps); its
                payload is a WES run request (cf. `foca.plugins.wes`).
            ctx: Context of the call.

        Raises:
            NotApplicable: The run's `workflow_url` is no TRS URI.
            Rejected: The TRS URI is malformed or its `workflow_type` has no
                TRS descriptor type (`400`), its host is not allowed
                (`403`), the TRS has no such tool version or descriptors, or
                they are unusable (`422`), they are too many or too large
                (`413`), or a fetched file has the name of an attached one
                (`409`).
            TrsError: The TRS answered unexpectedly (e.g., `500`).
            RuntimeError: The context has no HTTP client.
            foca.utils.http.HttpError: A request failed (e.g., timeout).
        """
        settings = self.settings  # once, as setup() may replace it
        run = request.payload
        url = run.get(WORKFLOW_URL) if isinstance(run, dict) else None
        if not isinstance(url, str):
            raise NotApplicable("no workflow_url")
        try:
            tool = parse_tool_version(url)
        except InvalidTrsUri as exc:
            raise Rejected(f"{WORKFLOW_URL}: {exc}", 400) from None
        if tool is None:
            raise NotApplicable("workflow_url is no TRS URI")
        check_host(tool.host, settings.allowed_hosts, WORKFLOW_URL)
        type_ = descriptor_type(run.get("workflow_type"))
        if type_ is None:
            types = ", ".join(sorted(set(DESCRIPTOR_TYPES.values())))
            raise Rejected(
                f"workflow_type: no TRS descriptor type; use one of {types}", 400
            )
        if ctx.http is None:
            raise RuntimeError("No HTTP client in the context")
        files = fetch_descriptors(
            ctx.http, tool, type_, FileLimits(settings.max_files, settings.max_size)
        )
        attach_workflow(run, files, files[0].filename)
        ctx.record("trs_host", tool.host)
        ctx.record("files", len(files))
        ctx.record("bytes", sum(file.size for file in files))
