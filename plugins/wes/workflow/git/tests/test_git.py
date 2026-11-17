"""Tests of the plugin `wes.workflow.git` with recorded archives.

The hosting services are FOCA's `FakeHttp` (the test stand-in of
`ctx.http`), serving the recorded archives of `data/` (cf.
`test_archive.py`) at the archive URLs of each service. Unit tests call
the hook directly; the others run the plugin through FOCA's engine: its
contract checks, a harness, and a pipeline that loads the plugin through
its installed entry point, as gateway apps do.
"""

from collections.abc import Iterator, Sequence
from importlib.metadata import entry_points
from pathlib import Path
import time
from typing import Any

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
from pydantic import BaseModel, ValidationError
import pytest

from foca_plugins.wes.workflow.git import Git, GitConfig, GitError

PLUGIN_ID = "wes.workflow.git"
DATA = Path(__file__).parent / "data"
GITLAB_HOST = "git.example.org"
CONFIG = {"allowed_hosts": ["github.com", "gitlab.com", "bitbucket.org", GITLAB_HOST]}
GITHUB_URL = "https://github.com/org/workflows/blob/main/workflows/main.cwl"
GITHUB_ARCHIVE = "https://codeload.github.com/org/workflows/tar.gz/main"

SERVICES = [
    (GITHUB_URL, GITHUB_ARCHIVE, "github", "github.com"),
    (
        "https://gitlab.com/group/workflows/-/blob/feature%2Fx/workflows/main.cwl",
        "https://gitlab.com/group/workflows/-/archive/feature%2Fx/archive.tar.gz",
        "gitlab",
        "gitlab.com",
    ),
    (
        f"https://{GITLAB_HOST}/a/b/workflows/-/blob/main/workflows/main.cwl",
        f"https://{GITLAB_HOST}/a/b/workflows/-/archive/main/archive.tar.gz",
        "gitlab",
        GITLAB_HOST,
    ),
    (
        "https://bitbucket.org/example/workflows/src/449192eccfb6/workflows/main.cwl",
        "https://bitbucket.org/example/workflows/get/449192eccfb6.tar.gz",
        "bitbucket",
        "bitbucket.org",
    ),
]
"""URLs of the main file and of the archive, recorded archive and host."""

REPO_FILES = [
    "README.md",
    "tools/shared.cwl",
    "workflows/main.cwl",
    "workflows/tools/step.cwl",
]
"""Files of the recorded repository, in the order of its archives."""

REPO_SIZE = sum(
    len(content)
    for content in (
        b"# Example workflows\n",
        b'cwlVersion: v1.2\nclass: CommandLineTool\nbaseCommand: "false"\n'
        b"inputs: []\noutputs: []\n",
        b"cwlVersion: v1.2\nclass: Workflow\ninputs: []\noutputs: []\nsteps:\n"
        b"  step:\n    run: tools/step.cwl\n    in: []\n    out: []\n",
        b'cwlVersion: v1.2\nclass: CommandLineTool\nbaseCommand: "true"\n'
        b"inputs: []\noutputs: []\n",
    )
)
"""Total size of the files of the recorded repository."""


def serve(http: FakeHttp, url: str = GITHUB_ARCHIVE, name: str = "github") -> None:
    """Serve a recorded archive.

    Args:
        http: Fake HTTP client.
        url: URL of the archive.
        name: Name of the recorded archive.
    """
    http.add(
        "GET",
        url,
        content=(DATA / f"{name}.tar.gz").read_bytes(),
        headers={"Content-Type": "application/x-gzip"},
    )


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
    workflow_url: str = GITHUB_URL, attachments: Sequence[Attachment] = ()
) -> InboundRequest:
    """Build the inbound request of a WES run, new for each use.

    Args:
        workflow_url: `workflow_url`.
        attachments: Files of the client.

    Returns:
        Inbound request.
    """
    payload = make_run_request(
        workflow_url, workflow_type="CWL", attachments=list(attachments)
    )
    return make_inbound(payload, api="wes")


def make_plugin(**config: Any) -> Git:
    """Create the plugin, allowing the hosts of the tests.

    Args:
        config: Settings other than the defaults of the tests.

    Returns:
        Plugin.
    """
    return Git(PLUGIN_ID, GitConfig.model_validate({**CONFIG, **config}))


@pytest.fixture
def http() -> FakeHttp:
    """Create a fake HTTP client.

    Returns:
        Client.
    """
    return FakeHttp()


@pytest.fixture
def plugin() -> Git:
    """Create the plugin, allowing the hosts of the tests.

    Returns:
        Plugin.
    """
    return make_plugin()


# Settings and metadata


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is Git


def test_plugin_class_is_valid() -> None:
    """FOCA accepts the class for WES gateways."""
    assert check_plugin_class(Git, PLUGIN_ID, "wes") is Git
    assert Git.phases() == ("request",)
    title, description = Git.summary()
    assert title == "Run workflows from Git repositories."
    assert description.startswith("Runs workflows from public Git repositories")


def test_settings_have_defaults() -> None:
    """No host is allowed by default; limits have defaults."""
    plugin = Git(PLUGIN_ID, GitConfig())
    assert plugin.settings.allowed_hosts == frozenset()
    assert plugin.settings.directory is False
    assert plugin.settings.max_files == 100
    assert plugin.settings.max_size == 10 * 1024 * 1024
    assert plugin.settings.max_archive_size == 100 * 1024 * 1024


def test_settings_normalize_hosts() -> None:
    """Hosts are lowercase, without the port 443."""
    config = GitConfig(allowed_hosts=["GitHub.com", "a.org:443", "b.org:8443"])
    assert config.allowed_hosts == ["github.com", "a.org", "b.org:8443"]


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (GitConfig, {"allowed_hosts": ["https://github.com"]}),
        (GitConfig, {"allowed_hosts": ["github.com/org"]}),
        (GitConfig, {"allowed_hosts": ["github.com:99999"]}),
        (GitConfig, {"allowed_hosts": ["[::1]"]}),
        (GitConfig, {"attach": "files"}),
        (GitConfig, {"max_files": 0}),
        (GitConfig, {"max_files": 10_001}),
        (GitConfig, {"max_size": 0}),
        (GitConfig, {"max_archive_size": 0}),
        (GitConfig, {"other": "value"}),
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


@pytest.mark.parametrize(("url", "archive_url", "name", "host"), SERVICES)
def test_on_request_attaches_repository(
    plugin: Git, http: FakeHttp, url: str, archive_url: str, name: str, host: str
) -> None:
    """The run gets the files of the repository and points to the main file.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
        url: `workflow_url`.
        archive_url: URL of the archive.
        name: Name of the recorded archive.
        host: Host of the URL.
    """
    serve(http, archive_url, name)
    params = Attachment("params.json", b"{}", "application/json")
    request = make_run(url, attachments=[params])
    ctx = make_context(http)
    plugin.on_request(request, ctx)
    run = request.payload
    assert run["workflow_url"] == "workflows/main.cwl"
    assert [a.filename for a in run["workflow_attachment"]] == [
        "params.json",
        *REPO_FILES,
    ]
    check_run_request(run)
    assert [call.url for call in http.calls] == [archive_url]
    assert ctx.annotations == {"git_host": host, "files": 4, "bytes": REPO_SIZE}


def test_on_request_attaches_directory(http: FakeHttp) -> None:
    """With `attach: directory`, the main file's directory is attached.

    Args:
        http: Fake HTTP client.
    """
    serve(http)
    request = make_run()
    make_plugin(attach="directory").on_request(request, make_context(http))
    run = request.payload
    assert run["workflow_url"] == "main.cwl"
    assert [a.filename for a in run["workflow_attachment"]] == [
        "main.cwl",
        "tools/step.cwl",
    ]


def test_on_request_attaches_repository_for_files_at_root(http: FakeHttp) -> None:
    """Main files at the root take the whole repository as their directory.

    Args:
        http: Fake HTTP client.
    """
    serve(http, "https://codeload.github.com/o/r/tar.gz/main")
    request = make_run("https://github.com/o/r/blob/main/README.md")
    make_plugin(attach="directory").on_request(request, make_context(http))
    run = request.payload
    assert run["workflow_url"] == "README.md"
    assert [a.filename for a in run["workflow_attachment"]] == REPO_FILES


@pytest.mark.parametrize(
    "payload",
    [
        make_run_request("main.cwl"),
        make_run_request("https://example.org/main.cwl", attachments=[]),
        make_run_request("https://raw.githubusercontent.com/o/r/main/main.cwl"),
        make_run_request("https://github.com/o/r"),
        make_run_request("https://other.example.org/g/p/-/blob/main/main.cwl"),
        make_run_request("trs://trs.example.org/t/v"),
        {"workflow_attachment": []},
        {"workflow_url": None, "workflow_attachment": []},
        None,
        [],
    ],
)
def test_on_request_ignores_other_runs(
    plugin: Git, http: FakeHttp, payload: Any
) -> None:
    """Runs without repository URL are left to the next alternative.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
        payload: Payload of the inbound request.
    """
    with pytest.raises(NotApplicable):
        plugin.on_request(make_inbound(payload, api="wes"), make_context(http))
    assert http.calls == []


@pytest.mark.parametrize(
    ("url", "status", "detail"),
    [
        (
            "https://github.com/o/r/blob/main/w.cwl#L1",
            400,
            "workflow_url: repository URL has a query or fragment",
        ),
        (
            "https://github.com/o/r/blob/main/../w.cwl",
            400,
            "workflow_url: repository URL has an unsafe file path",
        ),
    ],
)
def test_on_request_rejects_malformed_urls(
    plugin: Git, http: FakeHttp, url: str, status: int, detail: str
) -> None:
    """Malformed repository URLs are rejected.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
        url: `workflow_url`.
        status: Expected status.
        detail: Expected message.
    """
    with pytest.raises(Rejected) as info:
        plugin.on_request(make_run(url), make_context(http))
    assert (info.value.status, info.value.detail) == (status, detail)
    assert http.calls == []


@pytest.mark.parametrize(
    "url",
    [
        GITHUB_URL,
        "https://gitlab.com/g/p/-/blob/main/main.cwl",
        "https://bitbucket.org/w/r/src/main/main.cwl",
    ],
)
def test_on_request_rejects_all_hosts_by_default(http: FakeHttp, url: str) -> None:
    """Without allowed hosts, the URLs of the known services are rejected.

    Args:
        http: Fake HTTP client.
        url: `workflow_url`.
    """
    plugin = Git(PLUGIN_ID, GitConfig())
    with pytest.raises(Rejected, match="host not allowed") as info:
        plugin.on_request(make_run(url), make_context(http))
    assert info.value.status == 403
    assert http.calls == []


def test_on_request_rejects_missing_files(plugin: Git, http: FakeHttp) -> None:
    """Main files that the repository lacks are rejected (`422`).

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    serve(http)
    request = make_run(GITHUB_URL.replace("main.cwl", "other.cwl"))
    with pytest.raises(Rejected, match="no such file in the repository") as info:
        plugin.on_request(request, make_context(http))
    assert info.value.status == 422


def test_on_request_rejects_missing_refs(plugin: Git, http: FakeHttp) -> None:
    """Refs or repositories that the host lacks are rejected (`422`).

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    http.add("GET", GITHUB_ARCHIVE, status=404, content=b"404: Not Found")
    with pytest.raises(Rejected, match="no such public repository or ref") as info:
        plugin.on_request(make_run(), make_context(http))
    assert info.value.status == 422


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"max_files": 3}, "more than 3 files"),
        ({"max_size": 10}, "larger than 10 bytes"),
        ({"max_archive_size": 1000}, "larger than 1000 bytes uncompressed"),
    ],
)
def test_on_request_applies_limits(
    http: FakeHttp, config: dict[str, Any], message: str
) -> None:
    """Repositories beyond the limits are rejected (`413`).

    Args:
        http: Fake HTTP client.
        config: Settings.
        message: Part of the message.
    """
    serve(http)
    with pytest.raises(Rejected, match=message) as info:
        make_plugin(**config).on_request(make_run(), make_context(http))
    assert info.value.status == 413


def test_on_request_rejects_conflicting_attachments(
    plugin: Git, http: FakeHttp
) -> None:
    """Fetched files can't replace files of the client (`409`).

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    serve(http)
    request = make_run(attachments=[Attachment("readme.md", b"x")])
    with pytest.raises(Rejected, match="conflicts with another") as info:
        plugin.on_request(request, make_context(http))
    assert info.value.status == 409
    assert request.payload["workflow_url"] == GITHUB_URL


def test_on_request_fails_on_host_errors(plugin: Git, http: FakeHttp) -> None:
    """Unexpected answers of the host fail the call.

    Args:
        plugin: Plugin.
        http: Fake HTTP client.
    """
    http.add("GET", GITHUB_ARCHIVE, status=503)
    with pytest.raises(GitError, match="github answered 503"):
        plugin.on_request(make_run(), make_context(http))


def test_on_request_needs_http_client(plugin: Git) -> None:
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
        Git,
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
        Git, config=CONFIG, plugin_id=PLUGIN_ID, api="wes", http=http
    )
    run = harness.run_request(make_run()).payload
    assert run["workflow_url"] == "workflows/main.cwl"
    assert len(run["workflow_attachment"]) == 4
    (execution,) = harness.executions
    assert (execution.phase, execution.outcome) == ("request", "ok")
    assert execution.annotations["files"] == 4


def test_harness_fails_on_host_errors(http: FakeHttp) -> None:
    """Host errors fail the call; the run is unchanged.

    Args:
        http: Fake HTTP client.
    """
    http.add("GET", GITHUB_ARCHIVE, status=503)
    harness = PluginHarness(
        Git,
        config=CONFIG,
        plugin_id=PLUGIN_ID,
        api="wes",
        http=http,
        on_failure="skip",
    )
    run = harness.run_request(make_run()).payload
    assert run["workflow_url"] == GITHUB_URL
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
            "pipeline": [{"id": "workflow", "alternatives": [{"plugin": PLUGIN_ID}]}],
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
    assert payload["workflow_url"] == "workflows/main.cwl"
    assert [a.filename for a in payload["workflow_attachment"]] == REPO_FILES
    assert [(e.phase, e.outcome) for e in run.executions] == [("request", "ok")]
