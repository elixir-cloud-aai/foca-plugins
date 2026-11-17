"""Tests of the plugin `wes.workflow.trs` with a fake TRS.

The fake TRS is FOCA's `FakeHttp` (the test stand-in of `ctx.http`) with
the responses of a TRS v2 for tool versions: their files
(`GET .../{type}/files`) and descriptors (`GET .../{type}/descriptor/
{relative_path}`, as JSON `FileWrapper`s). Unit tests call the fetch and
the hook directly; the others run the plugin through FOCA's engine: its
contract checks, a harness, and a pipeline that loads the plugin through
its installed entry point, as gateway apps do.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points
import time
from typing import Any
from urllib.parse import quote

from foca.plugins import (
    Attachment,
    Context,
    InboundRequest,
    NotApplicable,
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
    make_inbound,
    make_run_request,
)
from foca.plugins.wes import check_run_request
from foca.utils.http import RequestNotSent
from pydantic import BaseModel, ValidationError
import pytest

from foca_plugins.wes.workflow.trs import Trs, TrsConfig, TrsError
from foca_plugins.wes.workflow.trs.attach import FileLimits
from foca_plugins.wes.workflow.trs.trs import ToolVersion, fetch_descriptors

PLUGIN_ID = "wes.workflow.trs"
HOST = "trs.example.org"
CONFIG = {"allowed_hosts": [HOST]}
BASE = f"https://{HOST}/ga4gh/trs/v2"
TOOL = ToolVersion(HOST, BASE, "#workflow/github.com/o/r", "v1")
TOOL_URL = f"{BASE}/tools/%23workflow%2Fgithub.com%2Fo%2Fr/versions/v1"
TRS_URI = f"trs://{HOST}/%23workflow%2Fgithub.com%2Fo%2Fr/v1"

MAIN_CWL = "cwlVersion: v1.2\nclass: Workflow\nsteps: {}\n"
STEP_CWL = "cwlVersion: v1.2\nclass: CommandLineTool\n"


@dataclass(frozen=True)
class TrsFile:
    """File of a tool version in the fake TRS.

    Attributes:
        path: Path, as the TRS lists it.
        file_type: TRS file type.
        content: Content of descriptors; `None` for other files.
    """

    path: str
    file_type: str = "SECONDARY_DESCRIPTOR"
    content: str | None = STEP_CWL


CWL_FILES = (
    TrsFile("main.cwl", "PRIMARY_DESCRIPTOR", MAIN_CWL),
    TrsFile("tools/step.cwl"),
    TrsFile("tools/nested/deeper/sub.cwl", content="class: Workflow\n"),
    TrsFile("test.json", "TEST_FILE", None),
    TrsFile("Dockerfile", "CONTAINERFILE", None),
    TrsFile("README.md", "OTHER", None),
    TrsFile("io-schema.json", "IO_SCHEMA", None),
)
"""A CWL workflow with nested descriptors and files that are no descriptors."""


def serve(
    http: FakeHttp,
    files: Sequence[TrsFile] = CWL_FILES,
    type_: str = "CWL",
    tool_url: str = TOOL_URL,
) -> None:
    """Add the responses of a tool version to the fake TRS.

    Args:
        http: Fake HTTP client.
        files: Files of the tool version.
        type_: TRS descriptor type.
        tool_url: URL of the tool version.
    """
    listing = [{"path": f.path, "file_type": f.file_type} for f in files]
    http.add("GET", f"{tool_url}/{type_}/files", json=listing)
    for file in files:
        if file.content is not None:
            path = quote(file.path.lstrip("/"), safe="")
            http.add(
                "GET",
                f"{tool_url}/{type_}/descriptor/{path}",
                json={"content": file.content, "checksum": []},
            )


def descriptor_url(path: str, type_: str = "CWL") -> str:
    """Get the URL of a descriptor of the tool version.

    Args:
        path: Relative path.
        type_: TRS descriptor type.

    Returns:
        URL.
    """
    return f"{TOOL_URL}/{type_}/descriptor/{quote(path, safe='')}"


def fetch(
    http: FakeHttp, type_: str = "CWL", max_files: int = 100, max_size: int = 10**6
) -> list[Attachment]:
    """Fetch the descriptors of the tool version.

    Args:
        http: Fake HTTP client.
        type_: TRS descriptor type.
        max_files: Maximum number of files.
        max_size: Maximum total size.

    Returns:
        Files.
    """
    return fetch_descriptors(http, TOOL, type_, FileLimits(max_files, max_size))


def make_context(http: FakeHttp | None) -> Context:
    """Build the context of a hook call.

    Args:
        http: HTTP client of the call.

    Returns:
        Context.
    """
    return Context(
        api="wes",
        plugin_id=PLUGIN_ID,
        principal=Principal(subject="alice"),
        params=EmptyModel(),
        original={},
        deadline=time.monotonic() + 10,
        state={},
        request_id="r1",
        http=http,
    )


def make_run(
    workflow_url: str = TRS_URI,
    workflow_type: str = "CWL",
    attachments: Sequence[Attachment] = (),
) -> InboundRequest:
    """Build the inbound request of a WES run, new for each use.

    Args:
        workflow_url: `workflow_url`.
        workflow_type: `workflow_type`.
        attachments: Files of the client.

    Returns:
        Inbound request.
    """
    payload = make_run_request(
        workflow_url, workflow_type=workflow_type, attachments=list(attachments)
    )
    return make_inbound(payload, api="wes")


@pytest.fixture
def http() -> FakeHttp:
    """Create a fake HTTP client.

    Returns:
        Client.
    """
    return FakeHttp()


@pytest.fixture
def plugin() -> Trs:
    """Create the plugin, allowing the host of the fake TRS.

    Returns:
        Plugin.
    """
    return Trs(PLUGIN_ID, TrsConfig.model_validate(CONFIG))


# Fetching


def test_fetch_cwl_with_nested_descriptors(http: FakeHttp) -> None:
    """The primary and secondary descriptors are fetched, nothing else.

    Args:
        http: Fake HTTP client.
    """
    serve(http)
    files = fetch(http)
    assert [(f.filename, f.content, f.media_type) for f in files] == [
        ("main.cwl", MAIN_CWL.encode(), "text/plain"),
        ("tools/step.cwl", STEP_CWL.encode(), "text/plain"),
        ("tools/nested/deeper/sub.cwl", b"class: Workflow\n", "text/plain"),
    ]
    assert [call.url for call in http.calls] == [
        f"{TOOL_URL}/CWL/files",
        descriptor_url("main.cwl"),
        descriptor_url("tools/step.cwl"),
        descriptor_url("tools/nested/deeper/sub.cwl"),
    ]
    assert all(call.method == "GET" for call in http.calls)


def test_fetch_wdl(http: FakeHttp) -> None:
    """WDL descriptors come from the `WDL` endpoints; leading slashes go.

    Args:
        http: Fake HTTP client.
    """
    serve(
        http,
        [
            TrsFile("/tasks/align.wdl", content="task align {}"),
            TrsFile("/main.wdl", "PRIMARY_DESCRIPTOR", "workflow main {}"),
        ],
        type_="WDL",
    )
    files = fetch(http, "WDL")
    assert [(f.filename, f.content) for f in files] == [
        ("main.wdl", b"workflow main {}"),
        ("tasks/align.wdl", b"task align {}"),
    ]
    assert http.calls[1].url == descriptor_url("main.wdl", "WDL")


def test_fetch_unknown_tool_version(http: FakeHttp) -> None:
    """Unknown tools, versions or types are rejected with `422`.

    Args:
        http: Fake HTTP client.
    """
    http.add("GET", f"{TOOL_URL}/CWL/files", status=404, json={"code": 404})
    with pytest.raises(Rejected) as info:
        fetch(http)
    assert info.value.status == 422
    assert info.value.detail == (
        "workflow_url: TRS has no such tool version with type CWL"
    )


def test_fetch_missing_descriptor(http: FakeHttp) -> None:
    """Listed descriptors that the TRS doesn't have are rejected (`422`).

    Args:
        http: Fake HTTP client.
    """
    serve(http, [TrsFile("main.cwl", "PRIMARY_DESCRIPTOR", None)])
    http.add("GET", descriptor_url("main.cwl"), status=404)
    with pytest.raises(Rejected, match="lacks a descriptor") as info:
        fetch(http)
    assert info.value.status == 422


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ([TrsFile("a.cwl")], "no single primary descriptor"),
        (
            [
                TrsFile("a.cwl", "PRIMARY_DESCRIPTOR"),
                TrsFile("b.cwl", "PRIMARY_DESCRIPTOR"),
            ],
            "no single primary descriptor",
        ),
        (
            [TrsFile("main.cwl", "PRIMARY_DESCRIPTOR"), TrsFile("../x.cwl")],
            "fetched file: filename has a '.' or '..' segment",
        ),
        (
            [TrsFile("main.cwl", "PRIMARY_DESCRIPTOR"), TrsFile("MAIN.cwl")],
            "lists a descriptor twice",
        ),
    ],
)
def test_fetch_refuses_unusable_listings(
    http: FakeHttp, files: list[TrsFile], message: str
) -> None:
    """Unusable listings are rejected with `422`, before fetching descriptors.

    Listings need one primary descriptor, and safe paths, each once.

    Args:
        http: Fake HTTP client.
        files: Files of the tool version.
        message: Part of the message.
    """
    serve(http, files)
    with pytest.raises(Rejected, match=message) as info:
        fetch(http)
    assert info.value.status == 422
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "wrapper",
    [
        {"url": "https://example.org/main.cwl"},
        {"content": None},
        {"content": 1},
        ["content"],
    ],
)
def test_fetch_refuses_descriptors_without_content(
    http: FakeHttp, wrapper: Any
) -> None:
    """Descriptors without inline content are rejected with `422`.

    The plugin doesn't follow `url`s, which may point to any host.

    Args:
        http: Fake HTTP client.
        wrapper: Response of the TRS.
    """
    serve(http, [TrsFile("main.cwl", "PRIMARY_DESCRIPTOR", None)])
    http.add("GET", descriptor_url("main.cwl"), json=wrapper)
    with pytest.raises(Rejected, match="descriptor without content") as info:
        fetch(http)
    assert info.value.status == 422
    assert len(http.calls) == 2


def test_fetch_refuses_invalid_text(http: FakeHttp) -> None:
    """Descriptors that are no valid Unicode text are rejected with `422`.

    Args:
        http: Fake HTTP client.
    """
    serve(http, [TrsFile("main.cwl", "PRIMARY_DESCRIPTOR", None)])
    http.add("GET", descriptor_url("main.cwl"), content=b'{"content": "\\ud800"}')
    with pytest.raises(Rejected, match="invalid text") as info:
        fetch(http)
    assert info.value.status == 422


def test_fetch_limits_number_of_files(http: FakeHttp) -> None:
    """Too many descriptors are rejected (`413`) before fetching any.

    Args:
        http: Fake HTTP client.
    """
    serve(http)
    with pytest.raises(Rejected, match="more than 2 files") as info:
        fetch(http, max_files=2)
    assert info.value.status == 413
    assert len(http.calls) == 1


def test_fetch_limits_total_size(http: FakeHttp) -> None:
    """Descriptors larger than allowed in total are rejected (`413`).

    Fetching stops at the first file beyond the limit.

    Args:
        http: Fake HTTP client.
    """
    serve(http)
    size = len(MAIN_CWL) + len(STEP_CWL)
    assert len(fetch(http, max_size=size + 16)) == 3
    with pytest.raises(Rejected, match=f"larger than {size} bytes") as info:
        fetch(http, max_size=size)
    assert info.value.status == 413
    assert len(http.calls) == 4 + 4


def test_fetch_limits_response_size() -> None:
    """Responses beyond the client's limit are rejected with `413`."""
    http = FakeHttp(max_response_size=64)
    serve(http, [TrsFile("main.cwl", "PRIMARY_DESCRIPTOR", "x" * 100)])
    with pytest.raises(Rejected, match="TRS response is too large") as info:
        fetch(http)
    assert info.value.status == 413


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"status": 500, "json": {}}, "TRS answered 500"),
        ({"status": 302, "headers": {"Location": "https://x/"}}, "TRS answered 302"),
        ({"status": 401}, "TRS answered 401"),
        ({"content": b"<html>"}, "invalid JSON"),
        ({"json": {"files": []}}, "files are no list"),
        ({"json": [{"file_type": "PRIMARY_DESCRIPTOR"}]}, "has no path"),
        ({"json": ["main.cwl"]}, "has no path"),
    ],
)
def test_fetch_fails_on_unexpected_responses(
    http: FakeHttp, response: dict[str, Any], message: str
) -> None:
    """Unexpected responses fail the call; redirects are not followed.

    Args:
        http: Fake HTTP client.
        response: Arguments of the canned response.
        message: Part of the message.
    """
    http.add("GET", f"{TOOL_URL}/CWL/files", **response)
    with pytest.raises(TrsError, match=message):
        fetch(http)
    assert len(http.calls) == 1


def test_fetch_passes_on_http_errors(http: FakeHttp) -> None:
    """Errors of the HTTP client (e.g., refused addresses) fail the call.

    Args:
        http: Fake HTTP client.
    """
    error = RequestNotSent("refused")
    http.add("GET", f"{TOOL_URL}/CWL/files", error=error)
    with pytest.raises(RequestNotSent):
        fetch(http)


# Settings and metadata


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is Trs


def test_plugin_class_is_valid() -> None:
    """FOCA accepts the class for WES gateways."""
    assert check_plugin_class(Trs, PLUGIN_ID, "wes") is Trs
    assert Trs.phases() == ("request",)
    title, description = Trs.summary()
    assert title == "Run workflows from a GA4GH TRS."
    assert description.startswith("Runs workflows registered in a GA4GH TRS")


def test_settings_have_defaults() -> None:
    """No host is allowed by default; limits have defaults."""
    plugin = Trs(PLUGIN_ID, TrsConfig())
    assert plugin.allowed_hosts == frozenset()
    assert plugin.max_files == 100
    assert plugin.max_size == 10 * 1024 * 1024


def test_settings_normalize_hosts() -> None:
    """Hosts are lowercase, without the port 443."""
    config = TrsConfig(allowed_hosts=["TRS.Example.org", "a.org:443", "b.org:8443"])
    assert config.allowed_hosts == ["trs.example.org", "a.org", "b.org:8443"]


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (TrsConfig, {"allowed_hosts": ["https://trs.example.org"]}),
        (TrsConfig, {"allowed_hosts": ["trs.example.org/path"]}),
        (TrsConfig, {"allowed_hosts": ["-trs.example.org"]}),
        (TrsConfig, {"allowed_hosts": ["user@trs.example.org"]}),
        (TrsConfig, {"allowed_hosts": ["trs.example.org:99999"]}),
        (TrsConfig, {"allowed_hosts": [""]}),
        (TrsConfig, {"allowed_hosts": [1]}),
        (TrsConfig, {"max_files": 0}),
        (TrsConfig, {"max_size": 0}),
        (TrsConfig, {"other": "value"}),
        (EmptyModel, {"other": "value"}),
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


# The hook


def test_on_request_attaches_workflow(plugin: Trs, http: FakeHttp) -> None:
    """The run gets the descriptors and points to the primary one.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    serve(http)
    params = Attachment("params.json", b"{}", "application/json")
    request = make_run(attachments=[params])
    ctx = make_context(http)
    plugin.on_request(request, ctx)
    run = request.payload
    assert run["workflow_url"] == "main.cwl"
    assert [a.filename for a in run["workflow_attachment"]] == [
        "params.json",
        "main.cwl",
        "tools/step.cwl",
        "tools/nested/deeper/sub.cwl",
    ]
    check_run_request(run)
    size = len(MAIN_CWL) + len(STEP_CWL) + len("class: Workflow\n")
    assert ctx.annotations == {"trs_host": HOST, "files": 3, "bytes": size}


def test_on_request_accepts_tool_version_urls(plugin: Trs, http: FakeHttp) -> None:
    """URLs of tool versions work as TRS URIs, with Nextflow as `NFL`.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    serve(http, [TrsFile("main.nf", "PRIMARY_DESCRIPTOR", "workflow {}")], "NFL")
    request = make_run(TOOL_URL, "Nextflow")
    plugin.on_request(request, make_context(http))
    assert request.payload["workflow_url"] == "main.nf"


@pytest.mark.parametrize(
    "payload",
    [
        make_run_request("main.cwl"),
        make_run_request("https://example.org/main.cwl", attachments=[]),
        {"workflow_attachment": []},
        {"workflow_url": None, "workflow_attachment": []},
        None,
        [],
    ],
)
def test_on_request_ignores_other_runs(
    plugin: Trs, http: FakeHttp, payload: Any
) -> None:
    """Runs without TRS URI are left to the next alternative.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
        payload: Payload of the inbound request.
    """
    with pytest.raises(NotApplicable):
        plugin.on_request(make_inbound(payload, api="wes"), make_context(http))
    assert http.calls == []


@pytest.mark.parametrize(
    ("request_", "status", "detail"),
    [
        (
            make_run("trs://trs.example.org/a"),
            400,
            "workflow_url: TRS URI is not trs://<host>/<tool>/<version>",
        ),
        (
            make_run("trs://other.example.org/t/v"),
            403,
            "workflow_url: host not allowed",
        ),
        (
            make_run("trs://trs.example.org:8443/t/v"),
            403,
            "workflow_url: host not allowed",
        ),
        (
            make_run(workflow_type="PYTHON"),
            400,
            "workflow_type: no TRS descriptor type; use one of CWL, GALAXY, NFL,"
            " SMK, WDL",
        ),
    ],
)
def test_on_request_rejects_runs(
    plugin: Trs, http: FakeHttp, request_: InboundRequest, status: int, detail: str
) -> None:
    """Malformed URIs, other hosts and other workflow types are rejected.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
        request_: Inbound request.
        status: Expected status.
        detail: Expected message.
    """
    with pytest.raises(Rejected) as info:
        plugin.on_request(request_, make_context(http))
    assert (info.value.status, info.value.detail) == (status, detail)
    assert http.calls == []


def test_on_request_rejects_all_hosts_by_default(http: FakeHttp) -> None:
    """Without allowed hosts, every TRS URI is rejected.

    Args:
        http: Fake HTTP client.
    """
    plugin = Trs(PLUGIN_ID, TrsConfig())
    with pytest.raises(Rejected, match="host not allowed"):
        plugin.on_request(make_run(), make_context(http))
    assert http.calls == []


def test_on_request_rejects_conflicting_attachments(
    plugin: Trs, http: FakeHttp
) -> None:
    """Fetched files can't replace files of the client (`409`).

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    serve(http)
    request = make_run(attachments=[Attachment("tools/step.cwl", b"x")])
    with pytest.raises(Rejected) as info:
        plugin.on_request(request, make_context(http))
    assert info.value.status == 409


def test_on_request_needs_http_client(plugin: Trs) -> None:
    """Contexts without HTTP client are a programming error.

    Args:
        plugin: Plugin.
    """
    with pytest.raises(RuntimeError, match="No HTTP client"):
        plugin.on_request(make_run(), make_context(None))


# Through the engine


def test_plugin_meets_contract(http: FakeHttp) -> None:
    """The plugin passes FOCA's contract checks (class, metadata, hooks).

    Args:
        http: Fake HTTP client.
    """
    serve(http)
    check_plugin_contract(
        Trs,
        config=CONFIG,
        plugin_id=PLUGIN_ID,
        api="wes",
        inbound=make_run(),
        http=http,
    )


def test_harness_runs_hook(http: FakeHttp) -> None:
    """Through the engine, the run gets the workflow and passes its checks.

    Args:
        http: Fake HTTP client.
    """
    serve(http)
    harness = PluginHarness(
        Trs, config=CONFIG, plugin_id=PLUGIN_ID, api="wes", http=http
    )
    run = harness.run_request(make_run()).payload
    assert run["workflow_url"] == "main.cwl"
    assert len(run["workflow_attachment"]) == 3
    (execution,) = harness.executions
    assert (execution.phase, execution.outcome) == ("request", "ok")
    assert execution.annotations["files"] == 3


def test_harness_fails_on_trs_errors(http: FakeHttp) -> None:
    """TRS errors fail the call; the run is unchanged.

    Args:
        http: Fake HTTP client.
    """
    http.add("GET", f"{TOOL_URL}/CWL/files", status=503)
    harness = PluginHarness(
        Trs,
        config=CONFIG,
        plugin_id=PLUGIN_ID,
        api="wes",
        http=http,
        on_failure="skip",
    )
    run = harness.run_request(make_run()).payload
    assert run["workflow_url"] == TRS_URI
    assert harness.executions[0].outcome == "error"


@pytest.fixture
def host(http: FakeHttp) -> Iterator[PluginHost]:
    """Create a plugin host that loads the installed plugin.

    Args:
        http: Fake HTTP client, as the host's `ctx.http`.

    Yields:
        Host whose default pipeline runs the plugin.
    """
    conf = PluginsConfig.model_validate(
        {
            "available": {PLUGIN_ID: {"config": CONFIG}},
            "pipeline": [{"id": "trs", "alternatives": [{"plugin": PLUGIN_ID}]}],
        }
    )
    host = PluginHost(conf, api="wes", http_client=http.client)
    yield host
    host.close()


def test_pipeline_runs_plugin(host: PluginHost, http: FakeHttp) -> None:
    """The engine loads the installed plugin and runs its hook.

    Args:
        host: Plugin host.
        http: Fake HTTP client.
    """
    serve(http)
    run = host.start(make_run(), Principal(subject="alice"), request_id="r1")
    payload = run.request().payload
    assert payload["workflow_url"] == "main.cwl"
    assert [a.filename for a in payload["workflow_attachment"]] == [
        "main.cwl",
        "tools/step.cwl",
        "tools/nested/deeper/sub.cwl",
    ]
    assert [(e.phase, e.outcome) for e in run.executions] == [("request", "ok")]
