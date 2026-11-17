"""Plugin `wes.workflow.git`: run WES workflows from Git repositories.

Clients of WES gateways pass the URL of a workflow's main file in a public
repository of GitHub, GitLab or Bitbucket, at a ref (branch, tag or
commit), as `workflow_url`, as the services' web pages show it. WES
doesn't define these, so most WES backends can't fetch them. In the
`request` phase, this plugin downloads the archive of the repository at the
ref, attaches its files (of the whole repository, or of the main file's
directory) to the run (`workflow_attachment`) and sets `workflow_url` to
the main file's relative path, as WES defines for attached workflows: every
backend then gets the same, self-contained run request.

Requests go through the gateway's guarded HTTP client (`ctx.http`: public
addresses and `https` only, no redirects, bounded response sizes, within
the step's deadline), for allowed hosts only. Archives are read in memory
within limits (cf. :py:mod:`foca_plugins.wes.workflow.git.archive`).

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from dataclasses import dataclass
from typing import Literal

from foca.plugins import Context, InboundRequest, NotApplicable, Plugin, Rejected
from foca.plugins.base import EmptyModel
from pydantic import BaseModel, ConfigDict, Field

from foca_plugins.wes.workflow.git.archive import (
    GitError,
    download_archive,
    read_archive,
)
from foca_plugins.wes.workflow.git.attach import (
    WORKFLOW_URL,
    FileLimits,
    Host,
    attach_workflow,
    check_host,
)
from foca_plugins.wes.workflow.git.repo import (
    InvalidRepoUrl,
    parse_repo_file,
)

__all__ = ["Git", "GitConfig", "GitError"]


class GitConfig(BaseModel):
    """Admin settings (`plugins.available."wes.workflow.git".config`).

    Attributes:
        allowed_hosts: Hosts of the repositories that workflows may come
            from: `github.com`, `gitlab.com`, `bitbucket.org` or the hosts of
            other GitLab instances, optionally with a port (no IPv6
            addresses); none by default, so that the plugin rejects every
            repository URL until the admin allows hosts.
        attach: Files to attach: those of the whole `repository`, or of the
            `directory` of the main file and its subdirectories.
        max_files: Maximum number of files of a workflow.
        max_size: Maximum total size of the files of a workflow, in bytes
            (keep it, with the files that clients attach, within the
            gateway's `plugins.attachments.max_size`).
        max_archive_size: Maximum size of the decompressed archive of a
            repository, with the files that are not attached, in bytes.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_hosts: list[Host] = Field(default_factory=list, max_length=256)
    attach: Literal["repository", "directory"] = "repository"
    max_files: int = Field(default=100, ge=1, le=10_000)
    max_size: int = Field(default=10 * 1024 * 1024, ge=1)
    max_archive_size: int = Field(default=100 * 1024 * 1024, ge=1)


@dataclass(frozen=True)
class Settings:
    """Settings of the plugin, from its admin settings (`GitConfig`).

    Attributes:
        allowed_hosts: Allowed hosts, normalized.
        directory: Whether to attach the main file's directory only.
        max_files: Maximum number of files.
        max_size: Maximum total size of the files, in bytes.
        max_archive_size: Maximum size of decompressed archives, in bytes.
    """

    allowed_hosts: frozenset[str]
    directory: bool
    max_files: int
    max_size: int
    max_archive_size: int


class Git(Plugin[GitConfig, EmptyModel]):
    """Run workflows from Git repositories.

    Attributes:
        settings: Settings, from `GitConfig`.
    """

    Config = GitConfig
    Params = EmptyModel
    apis = frozenset({"wes"})
    capabilities = frozenset({"git-workflows"})
    maturity = "experimental"
    description = (
        "Runs workflows from public Git repositories: for a 'workflow_url'"
        " that is the URL of a file at a ref of a repository on GitHub,"
        " GitLab or Bitbucket (as their web pages show it), downloads the"
        " repository's archive at the ref from an allowed host, attaches its"
        " files to the run and sets 'workflow_url' to the file's relative"
        " path. Does not apply to other runs."
    )
    data_transfers = (
        "The hosting services at the admin's 'allowed_hosts' (for github.com,"
        " codeload.github.com): the repository and ref of URLs in"
        " 'workflow_url'",
    )

    # Set by `setup()`, from the admin settings
    settings: Settings

    def setup(self, config: GitConfig) -> None:
        """Keep the settings; idempotent, as `setup()` may run more than once.

        The settings are replaced as a whole, so that calls never see a mix
        of two configurations.

        Args:
            config: Validated admin settings.
        """
        self.settings = Settings(
            allowed_hosts=frozenset(config.allowed_hosts),
            directory=config.attach == "directory",
            max_files=config.max_files,
            max_size=config.max_size,
            max_archive_size=config.max_archive_size,
        )

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Fetch the workflow of a repository URL and attach it to the run.

        Args:
            request: Inbound request (a copy, which the engine keeps); its
                payload is a WES run request (cf. `foca.plugins.wes`).
            ctx: Context of the call.

        Raises:
            NotApplicable: The run's `workflow_url` is no repository URL.
            Rejected: The repository URL is malformed (`400`), its host is
                not allowed (`403`), the host has no such public repository
                or ref, the file is not in it, or the archive is unusable
                (`422`), the files are too many or too large (`413`), or a
                fetched file has the name of an attached one (`409`).
            GitError: The host answered unexpectedly (e.g., `500`), or with
                an invalid archive.
            RuntimeError: The context has no HTTP client.
            foca.utils.http.HttpError: The request failed (e.g., timeout).
        """
        settings = self.settings  # once, as setup() may replace it
        run = request.payload
        url = run.get(WORKFLOW_URL) if isinstance(run, dict) else None
        if not isinstance(url, str):
            raise NotApplicable("no workflow_url")
        try:
            repo = parse_repo_file(url, settings.allowed_hosts)
        except InvalidRepoUrl as exc:
            raise Rejected(f"{WORKFLOW_URL}: {exc}", 400) from None
        if repo is None:
            raise NotApplicable("workflow_url is no repository URL")
        check_host(repo.host, settings.allowed_hosts, WORKFLOW_URL)
        if ctx.http is None:
            raise RuntimeError("No HTTP client in the context")
        directory, _, main_file = repo.path.rpartition("/")
        if settings.directory and directory:
            directory += "/"
        else:
            directory, main_file = "", repo.path
        content = download_archive(ctx.http, repo)
        files = read_archive(
            content,
            directory,
            FileLimits(settings.max_files, settings.max_size),
            settings.max_archive_size,
        )
        if all(file.filename != main_file for file in files):
            raise Rejected(f"{WORKFLOW_URL}: no such file in the repository", 422)
        attach_workflow(run, files, main_file)
        ctx.record("git_host", repo.host)
        ctx.record("files", len(files))
        ctx.record("bytes", sum(file.size for file in files))
