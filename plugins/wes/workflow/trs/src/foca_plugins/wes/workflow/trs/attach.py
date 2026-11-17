"""Attach fetched workflow files to WES run requests, within limits.

Helpers for `wes` plugins that fetch a workflow from elsewhere (a TRS, a
Git hosting service, ...) and attach its files to the run request
(`workflow_attachment`, cf. FOCA's `foca.plugins.wes`): which hosts are
allowed, the limits of the files, safe relative paths, and the change of
the run itself. They know nothing of TRS, so that other `wes.workflow`
plugins can reuse them (copy the module, or move it to a shared
distribution once a second plugin needs it).

Errors that clients can fix, or that they should learn about, are
rejections (:py:class:`foca.plugins.Rejected`), with messages that name
fields, never values.
"""

from collections.abc import Collection, Iterable, MutableMapping
from typing import Annotated, Any
import unicodedata
from urllib.parse import SplitResult, urlsplit

from foca.plugins import Attachment, Rejected
from foca.plugins.config import AttachmentsConfig
from foca.plugins.wes import (
    ATTACHMENT_FIELD,
    InvalidRunRequest,
    check_attachments,
    check_filename,
)
from pydantic import AfterValidator, BeforeValidator, StringConstraints

WORKFLOW_URL = "workflow_url"
"""Field of run requests with the workflow's URL or main file."""

_NO_LIMITS = AttachmentsConfig(max_count=2**31, max_size=2**62)


def host_key(url: SplitResult) -> str:
    """Get the host of a URL as entries of allowlists name it.

    Args:
        url: Parsed `https` URL.

    Returns:
        The host name, in lowercase, with the port if the URL has one other
        than 443 (e.g., `trs.example.org` or `trs.example.org:8443`); IPv6
        addresses in brackets.

    Raises:
        ValueError: The URL has no host, or an invalid port.
    """
    host = url.hostname
    if not host:
        raise ValueError("no host")
    if ":" in host:
        host = f"[{host}]"
    port = url.port
    return host if port in (None, 443) else f"{host}:{port}"


# Host names (letters, digits, hyphens, dots) or IPv4 addresses, optionally
# with a port; no IPv6 addresses
_HOST = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?"


def _lowercase(value: Any) -> Any:
    """Turn a host name into lowercase, as hosts of URLs are compared.

    Args:
        value: Host name, or another value (left to the validation).

    Returns:
        The value, in lowercase if it is a string.
    """
    return value.lower() if isinstance(value, str) else value


def _normalize(value: str) -> str:
    """Normalize an allowed host as hosts of URLs are (cf. `host_key()`).

    Args:
        value: Host name, optionally with a port.

    Returns:
        The host, without the port 443 of `https`.

    Raises:
        ValueError: The port is out of range.
    """
    try:
        return host_key(urlsplit(f"https://{value}"))
    except ValueError:
        raise ValueError("invalid port") from None


Host = Annotated[
    str,
    BeforeValidator(_lowercase),
    StringConstraints(pattern=rf"^{_HOST}$", max_length=259),
    AfterValidator(_normalize),
]


"""Allowed host of settings: a host name, optionally with a port, as
:py:func:`host_key` returns it (lowercase, without the port 443)."""


def check_host(host: str, allowed: Collection[str], field: str) -> None:
    """Check that a host is allowed.

    Args:
        host: Host, as returned by :py:func:`host_key`.
        allowed: Allowed hosts, in the same form.
        field: Field of the run request that names the host, for messages.

    Raises:
        Rejected: The host is not allowed (`403`).
    """
    if host not in allowed:
        raise Rejected(f"{field}: host not allowed", 403)


def relative_path(path: str) -> str:
    """Turn the path of a fetched file into an attachment filename.

    Leading slashes are removed, as some registries list the paths of a
    workflow from its root.

    Args:
        path: Path of the file, as the source lists it.

    Returns:
        Safe relative path (cf. :py:func:`foca.plugins.wes.check_filename`).

    Raises:
        Rejected: The path is unsafe, e.g., with a `..` segment (`422`).
    """
    try:
        return check_filename(path.lstrip("/"))
    except InvalidRunRequest as exc:
        raise Rejected(f"{ATTACHMENT_FIELD}: fetched file: {exc}", 422) from None


def file_key(filename: str) -> str:
    """Get the key that tells filenames apart, as FOCA compares them.

    Args:
        filename: Filename.

    Returns:
        The NFKC normalization of the filename, ignoring case.
    """
    return unicodedata.normalize("NFKC", filename).casefold()


class FileLimits:
    """Number and total size of fetched files, checked as they come in.

    Create one per request: it counts the files of that request.

    Attributes:
        max_files: Maximum number of files.
        max_size: Maximum total size of the files, in bytes.
        size: Total size of the files so far, in bytes.
    """

    def __init__(self, max_files: int, max_size: int) -> None:
        """Create the limits.

        Args:
            max_files: Maximum number of files.
            max_size: Maximum total size of the files, in bytes.
        """
        self.max_files = max_files
        self.max_size = max_size
        self.size = 0

    def check_count(self, count: int) -> None:
        """Check the number of files, before fetching them.

        Args:
            count: Number of files.

        Raises:
            Rejected: There are more files than allowed (`413`).
        """
        if count > self.max_files:
            raise Rejected(
                f"{ATTACHMENT_FIELD}: workflow has more than {self.max_files} files",
                413,
            )

    def too_large(self) -> Rejected:
        """Describe files larger than allowed.

        Returns:
            Rejection (`413`).
        """
        return Rejected(
            f"{ATTACHMENT_FIELD}: workflow files are larger than {self.max_size} bytes",
            413,
        )

    def add(self, size: int) -> None:
        """Count the size of a fetched file.

        Args:
            size: Size of the file, in bytes.

        Raises:
            Rejected: The files are larger than allowed (`413`).
        """
        self.size += size
        if self.size > self.max_size:
            raise self.too_large()


def attach_workflow(
    run: MutableMapping[str, Any], files: Iterable[Attachment], main_file: str
) -> None:
    """Add fetched files to a run request and point it to its main file.

    The files are added after those of the client; `workflow_url` becomes
    the filename of the main file, as WES defines for attached workflows.

    Args:
        run: Run request (payload of the inbound request), changed in place.
        files: Fetched files.
        main_file: Filename of the main file, one of `files`.

    Raises:
        Rejected: A file has the name of another (also after NFKC
            normalization and ignoring case), e.g., of one that the client
            attached, or is a directory of another, or in another, as FOCA
            checks them (cf. `foca.plugins.wes.check_attachments`) (`409`).
    """
    attachments = list(run.get(ATTACHMENT_FIELD) or [])
    names = {file_key(attachment.filename) for attachment in attachments}
    for file in files:
        key = file_key(file.filename)
        if key in names:
            raise Rejected(
                f"{ATTACHMENT_FIELD}: a fetched file has the name of another file",
                409,
            )
        names.add(key)
        attachments.append(file)
    try:
        # names only: the gateway checks its limits after the hook
        check_attachments(attachments, _NO_LIMITS)
    except InvalidRunRequest as exc:
        raise Rejected(f"fetched files conflict with others: {exc}", 409) from None
    run[ATTACHMENT_FIELD] = attachments
    run[WORKFLOW_URL] = main_file
