"""URLs of files in Git repositories of GitHub, GitLab and Bitbucket.

Recognizes `workflow_url`s that point to a file at a ref (branch, tag or
commit) of a repository, as the web pages of the hosting services show
them, and gives the URL of the repository's archive at that ref, which the
services serve over `https` without redirects (no `git` binary needed).
"""

from collections.abc import Collection
from dataclasses import dataclass
import re
from urllib.parse import quote, unquote, urlsplit

from foca.plugins.wes import InvalidRunRequest, check_filename

from foca_plugins.wes.workflow.git.attach import host_key

GITHUB = "github.com"
"""Host of GitHub."""

GITHUB_ARCHIVES = "codeload.github.com"
"""Host of the archives of GitHub's repositories."""

GITLAB = "gitlab.com"
"""Host of GitLab (other GitLab hosts are recognized by their URLs)."""

BITBUCKET = "bitbucket.org"
"""Host of Bitbucket Cloud."""

MAX_REF_SIZE = 255
"""Maximum size of refs, in UTF-8 bytes."""

# `/<owner>/<repo>/blob/<ref>/<path>` (GitHub) and
# `/<workspace>/<repo>/src/<ref>/<path>` (Bitbucket)
_GITHUB_PATH = re.compile(
    r"/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)"
)
_BITBUCKET_PATH = re.compile(
    r"/(?P<owner>[^/]+)/(?P<repo>[^/]+)/src/(?P<ref>[^/]+)/(?P<path>.+)"
)
# `/<namespace>/<project>/-/blob/<ref>/<path>` (GitLab), with nested groups
_GITLAB_PATH = re.compile(
    r"(?P<project>(?:/[^/]+){2,}?)/-/blob/(?P<ref>[^/]+)/(?P<path>.+)"
)
# Names of owners, repositories, groups and projects, as the services allow
# them (letters, digits, `_`, `-`, `.`); `.`, `..` and `-` excluded below
_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# Characters and sequences that refs can't have (`git check-ref-format`)
_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//|/\.|\.lock(/|$)")


class InvalidRepoUrl(ValueError):
    """A repository URL is malformed; the message has no value."""


@dataclass(frozen=True)
class RepoFile:
    """File at a ref of a Git repository.

    Attributes:
        host: Host of the URL, as allowlists name it (cf.
            :py:func:`foca_plugins.wes.workflow.git.attach.host_key`).
        service: Hosting service: `github`, `gitlab` or `bitbucket`.
        ref: Branch, tag or commit, decoded.
        path: Path of the file in the repository, decoded.
        archive_url: URL of the repository's `tar.gz` archive at the ref.
    """

    host: str
    service: str
    ref: str
    path: str
    archive_url: str


def _name(value: str) -> str:
    """Check the name of an owner, repository, group or project.

    Args:
        value: Name, as in the URL.

    Returns:
        The name.

    Raises:
        InvalidRepoUrl: The name has other characters than letters, digits,
            `_`, `-` and `.`, or is `.`, `..` or `-`.
    """
    if not _NAME.fullmatch(value) or value in (".", "..", "-"):
        raise InvalidRepoUrl("repository URL has an invalid owner or repository")
    return value


def _ref(value: str) -> str:
    """Decode and check a ref.

    Refs with `/` (e.g., `feature/x`) are given URL-encoded (`feature%2Fx`).

    Args:
        value: Ref, as in the URL.

    Returns:
        Ref, decoded.

    Raises:
        InvalidRepoUrl: The ref is no valid Git ref name, e.g., `..`, or has
            spaces or control characters (refs may start with `-`, as tags
            can).
    """
    try:
        ref = unquote(value, errors="strict")
    except UnicodeDecodeError:
        raise InvalidRepoUrl("repository URL has an invalid ref") from None
    if (
        not ref
        or len(ref.encode("utf-8", "surrogatepass")) > MAX_REF_SIZE
        or _REF_FORBIDDEN.search(ref)
        or ref.startswith(("/", "."))
        or ref.endswith(("/", "."))
        or ref == "@"
    ):
        raise InvalidRepoUrl("repository URL has an invalid ref")
    return ref


def _path(value: str) -> str:
    """Decode and check the path of the file.

    Args:
        value: Path, as in the URL.

    Returns:
        Path, decoded: a safe relative path (cf.
        :py:func:`foca.plugins.wes.check_filename`).

    Raises:
        InvalidRepoUrl: The path is unsafe, e.g., with a `..` segment.
    """
    try:
        return check_filename(unquote(value, errors="strict"))
    except (InvalidRunRequest, UnicodeDecodeError):
        raise InvalidRepoUrl("repository URL has an unsafe file path") from None


def _service(
    host: str, path: str, gitlab_hosts: Collection[str]
) -> tuple[str, re.Match[str]] | None:
    """Recognize the hosting service of a URL by its host and path.

    Args:
        host: Host (cf. `host_key()`).
        path: Path of the URL, encoded.
        gitlab_hosts: Hosts other than `gitlab.com` whose URLs of the GitLab
            form count as GitLab's.

    Returns:
        Service and the match of its URL form, or `None`.
    """
    if host == GITHUB:
        match = _GITHUB_PATH.fullmatch(path)
        return ("github", match) if match else None
    if host == BITBUCKET:
        match = _BITBUCKET_PATH.fullmatch(path)
        return ("bitbucket", match) if match else None
    if host != GITLAB and host not in gitlab_hosts:
        return None
    match = _GITLAB_PATH.fullmatch(path)
    return ("gitlab", match) if match else None


def parse_repo_file(url: str, gitlab_hosts: Collection[str] = ()) -> RepoFile | None:
    """Recognize the URL of a file at a ref of a Git repository.

    Accepts the URLs of files as the services' web pages show them:

    - `https://github.com/<owner>/<repo>/blob/<ref>/<path>`;
    - `https://<host>/<namespace>/<project>/-/blob/<ref>/<path>` (GitLab,
      at `gitlab.com` or one of `gitlab_hosts`, with nested groups);
    - `https://bitbucket.org/<workspace>/<repo>/src/<ref>/<path>`.

    Refs with `/` must be URL-encoded (e.g., `feature%2Fx`), as the ref ends
    at the next `/`; the ref may also be a commit.

    Args:
        url: `workflow_url` of a run request.
        gitlab_hosts: Hosts other than `gitlab.com` whose URLs of the GitLab
            form count as GitLab's (e.g., the allowed hosts), as hosts
            (cf. :py:func:`foca_plugins.wes.workflow.git.attach.host_key`).

    Returns:
        The file, or `None` if the URL is none of these (e.g., the filename
        of an attachment, a raw file or another `https` URL).

    Raises:
        InvalidRepoUrl: The URL has one of these forms, but is malformed
            (user info, a query or fragment, invalid names or ref, an unsafe
            path).
    """
    try:
        parts = urlsplit(url.strip())
        host = host_key(parts)
    except ValueError:
        return None
    if parts.scheme.lower() != "https":
        return None
    recognized = _service(host, parts.path, gitlab_hosts)
    if recognized is None:
        return None
    service, match = recognized
    if "@" in parts.netloc:
        raise InvalidRepoUrl("repository URL has user info")
    if parts.query or parts.fragment:
        raise InvalidRepoUrl("repository URL has a query or fragment")
    ref = _ref(match["ref"])
    path = _path(match["path"])
    encoded_ref = quote(ref, safe="")
    if service == "gitlab":
        project = "/".join(_name(s) for s in match["project"].split("/")[1:])
        archive_url = f"https://{host}/{project}/-/archive/{encoded_ref}/archive.tar.gz"
    else:
        owner, repo = _name(match["owner"]), _name(match["repo"])
        if service == "github":
            archive_url = (
                f"https://{GITHUB_ARCHIVES}/{owner}/{repo}/tar.gz/{encoded_ref}"
            )
        else:
            archive_url = f"https://{BITBUCKET}/{owner}/{repo}/get/{encoded_ref}.tar.gz"
    return RepoFile(host, service, ref, path, archive_url)
