"""Plugin `wes.routing.workflow-type`: route runs by their workflow type.

WES backends support some workflow types (e.g., CWL 1.2, Nextflow DSL2) and,
from WES 1.1 on, some workflow engines, which they list in their service
info (by default `GET /ga4gh/wes/v1/service-info`: `workflow_type_versions`,
`workflow_engine_versions`). In the `route` phase, this plugin keeps the
candidate backends that support the run's `workflow_type` and
`workflow_type_version`, and its `workflow_engine` and
`workflow_engine_version` if the run sets them, in their order.

Service infos are fetched with the gateway's guarded HTTP client
(`ctx.http`: public addresses and `https` only, unless the operator allows
more; no credentials; within the step's deadline) and cached per process,
with TTLs; the plugin needs no plugin store (and no database). Whether a
backend whose service info can't be read is kept or dropped is the admin's
choice.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from foca.plugins import Backend, Context, NotApplicable, Plugin, Rejected
from foca.plugins.base import EmptyModel
from foca.utils.http import GuardedClient, HttpError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from foca_plugins.wes.routing.workflow_type.service_info import (
    Run,
    ServiceInfoError,
    Support,
    SupportCache,
    fetch_support,
    run_needs,
)

__all__ = ["RESERVE", "WorkflowType", "WorkflowTypeConfig"]

# Absolute URL paths (RFC 3986: segments of unreserved and sub-delimiter
# characters, `:`, `@` and percent-encodings), without query or fragment
_PATH = r"^(/([A-Za-z0-9._~!$&'()*+,;=:@-]|%[0-9A-Fa-f]{2})+)+$"

RESERVE = 0.25
"""Seconds of the step's time kept for returning, as the engine discards
results that come after the deadline: no request runs into them."""

# Reasons of HTTP errors that mean that a request ran out of time
_TIMEOUTS = frozenset({"deadline_exceeded", "read_timeout"})


class WorkflowTypeConfig(BaseModel):
    """Admin settings (`plugins.available."wes.routing.workflow-type".config`).

    Attributes:
        service_info_path: Path of the service info below the base URL of
            each backend (FOCA's `Backend.url`, without the API's path).
        unreadable: What to do with backends whose service info can't be
            read (e.g., an error, a timeout, no `workflow_type_versions`):
            `keep` them, in their place, or `drop` them.
        cache_ttl: Seconds to keep the service info of a backend.
        failure_ttl: Seconds to keep a backend whose service info couldn't
            be read as unreadable, so that its failures aren't retried on
            every run (not if the step's deadline passed before the
            request).
        cache_size: Maximum number of backends in the cache of the process;
            0 turns the cache off.
        request_timeout: Seconds per service info request, so that one slow
            backend leaves time for the others; within the step's time (less
            `RESERVE`).
        max_size: Maximum size of a service info, in bytes (and at most the
            gateway's `plugins.defaults.http.max_response_size`), so that
            parsing it can't take up the step's time.
    """

    model_config = ConfigDict(extra="forbid")

    service_info_path: str = Field(
        default="/ga4gh/wes/v1/service-info", max_length=256, pattern=_PATH
    )
    unreadable: Literal["keep", "drop"] = "keep"
    cache_ttl: int = Field(default=300, ge=0, le=86400)
    failure_ttl: int = Field(default=30, ge=0, le=86400)
    cache_size: int = Field(default=1000, ge=0, le=100_000)
    request_timeout: float = Field(default=2.0, gt=0, le=60)
    max_size: int = Field(default=256 * 1024, ge=1024, le=16 * 1024 * 1024)

    @field_validator("service_info_path")
    @classmethod
    def _no_dot_segments(cls, path: str) -> str:
        """Refuse `.` and `..` segments, which would leave the base URL.

        Args:
            path: Path of the service info.

        Returns:
            The path.

        Raises:
            ValueError: The path has a `.` or `..` segment.
        """
        if any(segment in (".", "..") for segment in path.split("/")):
            raise ValueError("must not have '.' or '..' segments")
        return path


@dataclass(frozen=True)
class _Runtime:
    """Settings and cache of the plugin, replaced together by `setup()`.

    Attributes:
        settings: Validated admin settings.
        cache: Supports of the backends, by URL of their service info.
    """

    settings: WorkflowTypeConfig
    cache: SupportCache


@dataclass
class _Counts:
    """Counts of a call, for the annotations.

    Attributes:
        fetched: Service infos requested and answered, or failed (not
            skipped).
        cache_hits: Backends found in the cache.
        unreadable: Backends whose service info couldn't be read, also the
            skipped ones.
        skipped: Backends whose service info wasn't requested, or timed
            out with less than half of `request_timeout`, as the step's
            time was (nearly) up.
    """

    fetched: int = 0
    cache_hits: int = 0
    unreadable: int = 0
    skipped: int = 0


class WorkflowType(Plugin[WorkflowTypeConfig, EmptyModel]):
    """Route runs to the backends that support their workflow type.

    Attributes:
        runtime: Settings and cache, from the admin settings.
    """

    Config = WorkflowTypeConfig
    Params = EmptyModel
    apis = frozenset({"wes"})
    capabilities = frozenset({"routing-by-workflow-type"})
    maturity = "experimental"
    description = (
        "Keeps the candidate backends whose service info lists the run's"
        " workflow type and version, and its workflow engine and version if"
        " the run sets them, in their order. Backends whose service info"
        " can't be read are kept or dropped, as the admin configures. Rejects"
        " runs that no candidate supports. Does not apply to requests without"
        " a workflow type."
    )
    # Service infos come from the candidate backends themselves, requested
    # without request data; nothing in the plugin store
    data_transfers = ()
    data_stored = ()

    # Set by `setup()`, from the admin settings
    runtime: _Runtime

    def setup(self, config: WorkflowTypeConfig) -> None:
        """Keep the settings and create the cache.

        Idempotent, as `setup()` may run more than once; settings and cache
        are replaced together, so that calls never see a mix of two
        configurations.

        Args:
            config: Validated admin settings.
        """
        self.runtime = _Runtime(config, SupportCache(config.cache_size))

    def on_route(self, candidates: Sequence[Backend], ctx: Context) -> list[Backend]:
        """Keep the candidates that support the run.

        Args:
            candidates: Candidate backends, in order.
            ctx: Context of the call; `ctx.original` is the run request as
                submitted (cf. `foca.plugins.wes`).

        Returns:
            The candidates that support the run, and those whose service info
            can't be read unless `unreadable` is `drop`, in their order; none
            if all those were dropped as unreadable.

        Raises:
            NotApplicable: The request is no run request with a workflow
                type.
            Rejected: No candidate supports the run, and none was unreadable
                (`422`).
            RuntimeError: The context has no HTTP client.
        """
        runtime = self.runtime  # once, as setup() may replace it
        run = run_needs(ctx.original)
        if run is None:
            raise NotApplicable("no run request with a workflow_type")
        http = ctx.http
        if http is None:
            raise RuntimeError("No HTTP client in the context")
        counts = _Counts()
        kept: list[Backend] = []
        unsupported = 0
        for backend in candidates:
            support = _support(backend, runtime, http, ctx, counts)
            if support is None:
                if runtime.settings.unreadable == "keep":
                    kept.append(backend)
            elif support.supports(run):
                kept.append(backend)
            else:
                unsupported += 1
        ctx.record("candidates", len(candidates))
        ctx.record("kept", len(kept))
        ctx.record("unsupported", unsupported)
        ctx.record("unreadable", counts.unreadable)
        ctx.record("skipped", counts.skipped)
        ctx.record("fetched", counts.fetched)
        ctx.record("cache_hits", counts.cache_hits)
        if candidates and unsupported == len(candidates):
            raise Rejected(_unsupported_detail(run), 422)
        return kept


def _support(
    backend: Backend,
    runtime: _Runtime,
    http: GuardedClient,
    ctx: Context,
    counts: _Counts,
) -> Support | None:
    """Get what a backend supports, from the cache or its service info.

    Args:
        backend: Candidate backend.
        runtime: Settings and cache of the call.
        http: HTTP client of the call.
        ctx: Context of the call.
        counts: Counts of the call, updated.

    Returns:
        Supports of the backend; `None` if its service info can't be read.
    """
    settings = runtime.settings
    url = backend.url.rstrip("/") + settings.service_info_path
    cached, support = runtime.cache.get(url)
    if cached:
        counts.cache_hits += 1
    elif (budget := ctx.remaining() - RESERVE) <= 0:
        counts.skipped += 1
    else:
        # the client is the call's own, so its limits can be set per request:
        # small bodies, so that parsing them takes no noticeable time
        http.timeout = min(settings.request_timeout, budget)
        http.max_response_size = min(http.max_response_size, settings.max_size)
        try:
            support = fetch_support(http, url)
        except HttpError as exc:
            if exc.reason in _TIMEOUTS and budget < settings.request_timeout / 2:
                # out of the step's time rather than the backend's, with less
                # than half of its time: not cached, so the next run retries
                counts.skipped += 1
            else:
                _failed(backend, url, exc, runtime, ctx, counts)
        except ServiceInfoError as exc:
            _failed(backend, url, exc, runtime, ctx, counts)
        else:
            counts.fetched += 1
            runtime.cache.put(url, support, settings.cache_ttl)
    if support is None:
        counts.unreadable += 1
    return support


def _failed(
    backend: Backend,
    url: str,
    exc: Exception,
    runtime: _Runtime,
    ctx: Context,
    counts: _Counts,
) -> None:
    """Log and cache a backend whose service info couldn't be read.

    Args:
        backend: Candidate backend.
        url: URL of its service info.
        exc: Error of the request or of the service info.
        runtime: Settings and cache of the call.
        ctx: Context of the call.
        counts: Counts of the call, updated.
    """
    counts.fetched += 1
    ctx.log.warning("Service info of backend %s unreadable: %s", backend.id, exc)
    runtime.cache.put(url, None, runtime.settings.failure_ttl)


def _unsupported_detail(run: Run) -> str:
    """Describe for the client which fields no backend supports.

    The message names the fields, not their values, which the client knows.

    Args:
        run: What the run needs.

    Returns:
        Message.
    """
    fields = ["workflow_type"]
    if run.workflow_type_version is not None:
        fields.append("workflow_type_version")
    if run.workflow_engine is not None:
        fields.append("workflow_engine")
    if run.workflow_engine_version is not None:
        fields.append("workflow_engine_version")
    return "No backend supports the run's " + ", ".join(fields)
