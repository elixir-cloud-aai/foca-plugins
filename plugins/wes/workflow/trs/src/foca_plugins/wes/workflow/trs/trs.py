"""GA4GH TRS: tool version URIs and the descriptor files of tool versions.

Recognizes `workflow_url`s that point to a tool version of a [GA4GH Tool
Registry Service](https://github.com/ga4gh/tool-registry-service-schemas)
(TRS) v2, and fetches the descriptor files of a version through the
gateway's guarded HTTP client (`ctx.http`).
"""

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import SplitResult, quote, unquote, urlsplit

from foca.plugins import Attachment, Rejected
from foca.utils.http import GuardedClient, ResponseTooLarge

from foca_plugins.wes.workflow.trs.attach import (
    WORKFLOW_URL,
    FileLimits,
    file_key,
    host_key,
    relative_path,
)

API_PATH = "/ga4gh/trs/v2"
"""Path of the TRS API v2 on a host, as TRS URIs resolve to."""

DESCRIPTOR_TYPES = {
    "CWL": "CWL",
    "WDL": "WDL",
    "NFL": "NFL",
    "NEXTFLOW": "NFL",
    "SMK": "SMK",
    "SNAKEMAKE": "SMK",
    "GALAXY": "GALAXY",
}
"""TRS descriptor types by WES `workflow_type` (in uppercase)."""

PRIMARY = "PRIMARY_DESCRIPTOR"
SECONDARY = "SECONDARY_DESCRIPTOR"

MEDIA_TYPE = "text/plain"
"""Media type of the attached descriptor files."""

FIELD = WORKFLOW_URL

MAX_LISTING_SIZE = 1024 * 1024
"""Maximum size of the file listings of tool versions, in bytes (parsed
JSON takes many times more memory)."""

JSON_OVERHEAD = 6
"""Factor of the size of JSON strings to that of their text, at most (as
`\\uXXXX` escapes)."""

WRAPPER_OVERHEAD = 64 * 1024
"""Size of the other fields of `FileWrapper`s (e.g., checksums), at most."""

# `https://<host>[/<prefix>]/ga4gh/trs/v2/tools/<id>/versions/<version>`,
# with the ID and the version as one (encoded) segment each
_TOOL_VERSION_PATH = re.compile(
    rf"(?P<prefix>(?:/[^/]+)*?){re.escape(API_PATH)}"
    r"/tools/(?P<id>[^/]+)/versions/(?P<version>[^/]+)/?"
)
# `trs://<host>/<id>/<version>`
_TRS_URI_PATH = re.compile(r"/(?P<id>[^/]+)/(?P<version>[^/]+)")


class InvalidTrsUri(ValueError):
    """A `trs://` URI or TRS URL is malformed; the message has no value."""


class TrsError(RuntimeError):
    """The TRS answered unexpectedly, e.g., with `500` or invalid JSON."""


@dataclass(frozen=True)
class ToolVersion:
    """Tool version of a TRS.

    Attributes:
        host: Host of the TRS, as allowlists name it (cf.
            :py:func:`foca_plugins.wes.workflow.trs.attach.host_key`).
        base_url: URL of the TRS API, e.g.,
            `https://trs.example.org/ga4gh/trs/v2`.
        tool_id: Identifier of the tool, decoded.
        version_id: Identifier of the version, decoded.
    """

    host: str
    base_url: str
    tool_id: str
    version_id: str

    @property
    def url(self) -> str:
        """Get the URL of the tool version.

        Returns:
            URL, with the identifiers encoded as one segment each.
        """
        tool = quote(self.tool_id, safe="")
        version = quote(self.version_id, safe="")
        return f"{self.base_url}/tools/{tool}/versions/{version}"


def descriptor_type(workflow_type: Any) -> str | None:
    """Get the TRS descriptor type of a WES workflow type.

    Args:
        workflow_type: WES `workflow_type`, e.g., `CWL` or `Nextflow`.

    Returns:
        TRS descriptor type (`CWL`, `WDL`, `NFL`, `SMK` or `GALAXY`), or
        `None` if there is none.
    """
    if not isinstance(workflow_type, str):
        return None
    return DESCRIPTOR_TYPES.get(workflow_type.strip().upper())


def _segment(value: str) -> str:
    """Decode an identifier from a URL path segment.

    Args:
        value: Encoded segment.

    Returns:
        Identifier.

    Raises:
        InvalidTrsUri: The identifier is `.` or `..`, which clients would
            remove from paths.
    """
    decoded = unquote(value)
    if decoded in (".", ".."):
        raise InvalidTrsUri("tool or version identifier is '.' or '..'")
    return decoded


def _check_parts(parts: SplitResult) -> str:
    """Check the parts of a TRS URI or URL other than the path.

    Args:
        parts: Parsed URI or URL.

    Returns:
        Host (cf. :py:func:`foca_plugins.wes.workflow.trs.attach.host_key`).

    Raises:
        InvalidTrsUri: The URI or URL has user info, a query or a fragment,
            no host, or an invalid port.
    """
    if "@" in parts.netloc:
        raise InvalidTrsUri("TRS URI has user info")
    if parts.query or parts.fragment:
        raise InvalidTrsUri("TRS URI has a query or fragment")
    try:
        return host_key(parts)
    except ValueError:
        raise InvalidTrsUri("TRS URI has no valid host") from None


def parse_tool_version(url: str) -> ToolVersion | None:
    """Recognize the tool version of a TRS in a `workflow_url`.

    Accepts `trs://<host>/<tool id>/<version id>` (the TRS URI, which
    resolves to `https://<host>/ga4gh/trs/v2/...`) and
    `https://<host>[/<prefix>]/ga4gh/trs/v2/tools/<tool id>/versions/<version
    id>`, with the identifiers URL-encoded (e.g., `%2F` for `/` and `%23`
    for `#`).

    Args:
        url: `workflow_url` of a run request.

    Returns:
        The tool version, or `None` if the URL is no TRS URI (e.g., the
        filename of an attachment, or another `https` URL).

    Raises:
        InvalidTrsUri: The URL is a `trs://` URI, or an `https` URL of a
            tool version, but malformed.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme == "trs":
        match = _TRS_URI_PATH.fullmatch(parts.path)
        if match is None:
            raise InvalidTrsUri("TRS URI is not trs://<host>/<tool>/<version>")
        host = _check_parts(parts)
        base_url = f"https://{host}{API_PATH}"
    elif scheme == "https":
        match = _TOOL_VERSION_PATH.fullmatch(parts.path)
        if match is None:
            return None
        host = _check_parts(parts)
        base_url = f"https://{host}{match['prefix']}{API_PATH}"
    else:
        return None
    return ToolVersion(
        host=host,
        base_url=base_url,
        tool_id=_segment(match["id"]),
        version_id=_segment(match["version"]),
    )


def _get_json(
    http: GuardedClient, url: str, missing: str, max_size: int, too_large: Rejected
) -> Any:
    """Get a JSON document from the TRS.

    Args:
        http: HTTP client (`ctx.http`).
        url: URL.
        missing: Message of the rejection if the TRS answers `404`.
        max_size: Maximum size of the response body, in bytes, checked
            before it is parsed (the client's own limit applies as well).
        too_large: Rejection if the body is larger than `max_size`.

    Returns:
        Parsed JSON.

    Raises:
        Rejected: The TRS answers `404` (`422`), the response is larger than
            the client allows (`422`), or larger than `max_size`
            (`too_large`).
        TrsError: The TRS answers with another status than `200` or `404`,
            or with invalid JSON.
        foca.utils.http.HttpError: The request failed.
    """
    try:
        response = http.get(url, headers={"Accept": "application/json"})
    except ResponseTooLarge:
        raise Rejected(f"{FIELD}: a TRS response is too large", 422) from None
    if response.status == 404:
        raise Rejected(f"{FIELD}: {missing}", 422)
    if response.status != 200:
        raise TrsError(f"TRS answered {response.status}")
    if len(response.content) > max_size:
        raise too_large
    try:
        return response.json()
    except ValueError:
        raise TrsError("TRS answered with invalid JSON") from None


def _descriptor_paths(listing: Any) -> list[str]:
    """Select the descriptors in the files of a tool version.

    Args:
        listing: Files of the tool version, as the TRS lists them (a list of
            `ToolFile`s).

    Returns:
        The paths of the primary descriptor (first) and the secondary
        descriptors, in their order; other files (e.g., tests, container
        files, I/O schemas) are left out.

    Raises:
        Rejected: There is no primary descriptor, or more than one (`422`).
        TrsError: The listing is no list of objects with string `path`s.
    """
    if not isinstance(listing, list):
        raise TrsError("TRS files are no list")
    primary: list[str] = []
    secondary: list[str] = []
    for item in listing:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise TrsError("TRS file has no path")
        if item.get("file_type") == PRIMARY:
            primary.append(item["path"])
        elif item.get("file_type") == SECONDARY:
            secondary.append(item["path"])
    if len(primary) != 1:
        raise Rejected(
            f"{FIELD}: TRS tool version has no single primary descriptor", 422
        )
    return primary + secondary


def fetch_descriptors(
    http: GuardedClient,
    tool: ToolVersion,
    type_: str,
    limits: FileLimits,
) -> list[Attachment]:
    """Fetch the descriptor files of a tool version.

    Lists the files (`GET .../{type}/files`), then fetches the primary and
    the secondary descriptors (`GET .../{type}/descriptor/{relative_path}`,
    as JSON `FileWrapper`s with `content`), one after the other, within the
    limits.

    Args:
        http: HTTP client (`ctx.http`).
        tool: Tool version.
        type_: TRS descriptor type, e.g., `CWL`.
        limits: Limits of the files.

    Returns:
        The files, the primary descriptor first, with their relative paths
        as filenames.

    Raises:
        Rejected: The TRS has no such tool version or descriptor type, or
            not one of its files, a file has no content or an unsafe path,
            two have the same path, or a response is too large (`422`), or
            the files exceed the limits (`413`).
        TrsError: The TRS answered unexpectedly.
        foca.utils.http.HttpError: A request failed.
    """
    base = f"{tool.url}/{quote(type_, safe='')}"
    listing = _get_json(
        http,
        f"{base}/files",
        f"TRS has no such tool version with type {type_}",
        MAX_LISTING_SIZE,
        Rejected(f"{FIELD}: TRS lists too many files", 422),
    )
    paths = [relative_path(path) for path in _descriptor_paths(listing)]
    if len({file_key(path) for path in paths}) != len(paths):
        raise Rejected(f"{FIELD}: TRS lists a descriptor twice", 422)
    limits.check_count(len(paths))
    files: list[Attachment] = []
    for path in paths:
        wrapper = _get_json(
            http,
            f"{base}/descriptor/{quote(path, safe='')}",
            "TRS lacks a descriptor that it lists",
            (limits.max_size - limits.size) * JSON_OVERHEAD + WRAPPER_OVERHEAD,
            limits.too_large(),
        )
        content = wrapper.get("content") if isinstance(wrapper, dict) else None
        if not isinstance(content, str):
            raise Rejected(f"{FIELD}: TRS gives a descriptor without content", 422)
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:
            raise Rejected(
                f"{FIELD}: TRS gives a descriptor of invalid text", 422
            ) from None
        limits.add(len(data))
        files.append(Attachment(path, data, MEDIA_TYPE))
    return files
