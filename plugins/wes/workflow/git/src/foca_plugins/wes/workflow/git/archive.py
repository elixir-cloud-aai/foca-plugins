"""Archives of Git repositories: download and read them within limits.

Downloads the `tar.gz` archive of a repository at a ref through the
gateway's guarded HTTP client (`ctx.http`), and reads the files of the
repository, or of a directory of it, from the archive in memory, never
writing to disk. The archive is untrusted: it is decompressed as a stream,
whose size is checked while it is read (so that archive bombs stop at the
limit), and entries with unsafe paths (absolute, with `..` segments),
links (symbolic or hard) and other special entries are refused.
"""

from collections.abc import Callable
import gzip
import io
import tarfile
import zlib

from foca.plugins import Attachment, Rejected
from foca.utils.http import GuardedClient, ResponseTooLarge

from foca_plugins.wes.workflow.git.attach import (
    WORKFLOW_URL,
    FileLimits,
    check_names,
    relative_path,
)
from foca_plugins.wes.workflow.git.repo import RepoFile

MEDIA_TYPE = "application/octet-stream"
"""Media type of the attached files."""

_REGULAR = (tarfile.REGTYPE, tarfile.AREGTYPE)
_LINKS = (tarfile.SYMTYPE, tarfile.LNKTYPE)


class GitError(RuntimeError):
    """The hosting service answered unexpectedly, e.g., with `500`."""


class _BoundedReader:
    """Readable stream that fails once more than a limit has been read.

    Attributes:
        remaining: Bytes that may still be read.
    """

    def __init__(
        self, read: Callable[[int], bytes], limit: int, too_large: Rejected
    ) -> None:
        """Create the stream.

        Args:
            read: Function that reads at most a number of bytes from the
                source (e.g., the decompressed archive).
            limit: Maximum number of bytes to read.
            too_large: Rejection once more is read.
        """
        self._read = read
        self.remaining = limit
        self._too_large = too_large

    def read(self, size: int = -1) -> bytes:
        """Read from the source, at most one byte beyond the limit.

        Args:
            size: Maximum number of bytes; all (up to the limit) if negative.

        Returns:
            Bytes read.

        Raises:
            Rejected: More than the limit was read.
        """
        allowed = self.remaining + 1
        data = self._read(allowed if size < 0 else min(size, allowed))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise self._too_large
        return data


def download_archive(http: GuardedClient, repo: RepoFile) -> bytes:
    """Download the archive of a repository at a ref.

    Args:
        http: HTTP client (`ctx.http`).
        repo: File of the repository.

    Returns:
        The compressed archive.

    Raises:
        Rejected: The service has no such repository or ref, e.g., as the
            repository is private (`404`; `422`), or the archive is larger
            than the client allows (`413`).
        GitError: The service answered with another status than `200` or
            `404` (e.g., a redirect or `5xx`).
        foca.utils.http.HttpError: The request failed.
    """
    try:
        response = http.get(repo.archive_url)
    except ResponseTooLarge:
        raise Rejected(
            f"{WORKFLOW_URL}: repository archive is larger than the gateway allows",
            413,
        ) from None
    if response.status == 404:
        raise Rejected(
            f"{WORKFLOW_URL}: no such public repository or ref at the host", 422
        )
    if response.status != 200:
        raise GitError(f"{repo.service} answered {response.status}")
    return response.content


def _segments(name: str) -> list[str]:
    """Split the name of an archive entry into safe path segments.

    Args:
        name: Name of the entry.

    Returns:
        Segments.

    Raises:
        Rejected: The name is absolute, or has an empty, `.` or `..` segment
            (`422`).
    """
    segments = name.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise Rejected(f"{WORKFLOW_URL}: repository archive has an unsafe path", 422)
    return segments


def read_archive(
    content: bytes,
    directory: str,
    limits: FileLimits,
    max_archive_size: int,
) -> list[Attachment]:
    """Read the files of a directory of a repository from its archive.

    The archive is a `tar.gz` file, as `git archive` writes them: all
    entries below one top directory (e.g., `<repo>-<ref>/`), which is
    removed from the paths. Files outside `directory` are skipped. Entries
    are checked as they are read: unsafe paths anywhere in the archive, and
    links or special entries (devices, FIFOs, sparse files) among the files
    to read, are refused.

    Args:
        content: Compressed archive.
        directory: Directory whose files to read, with a trailing `/`, or
            `""` for the whole repository; the files' paths are relative to
            it.
        limits: Limits of the number and total size of the files read.
        max_archive_size: Maximum size of the decompressed archive (the
            `tar` stream with all entries, also those skipped), in bytes,
            checked while it is decompressed.

    Returns:
        The files, in the order of the archive, with their paths relative to
        `directory` as filenames.

    Raises:
        Rejected: The decompressed archive is larger than
            `max_archive_size`, or the files exceed the limits (`413`); an
            entry has an unsafe path, the archive has no single top
            directory, the files to read have a link, a special entry or
            names that conflict (`422`).
        GitError: The archive is no valid `tar.gz` file.
    """
    decompressed = gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb")
    stream = _BoundedReader(
        decompressed.read,
        max_archive_size,
        Rejected(
            f"{WORKFLOW_URL}: repository archive is larger than"
            f" {max_archive_size} bytes uncompressed",
            413,
        ),
    )
    files: list[Attachment] = []
    try:
        with tarfile.open(fileobj=stream, mode="r|") as tar:  # type: ignore[call-overload]
            top: str | None = None
            while (entry := tar.next()) is not None:
                tar.members.clear()  # entries are read once; keep no list
                segments = _segments(entry.name)
                if top is None:
                    top = segments[0]
                if segments[0] != top or (len(segments) == 1 and not entry.isdir()):
                    raise Rejected(
                        f"{WORKFLOW_URL}: repository archive has no single"
                        " top directory",
                        422,
                    )
                path = "/".join(segments[1:])
                if not path.startswith(directory) or entry.isdir():
                    continue
                if entry.type in _LINKS:
                    raise Rejected(
                        f"{WORKFLOW_URL}: repository has a symbolic or hard link"
                        " among the files to attach",
                        422,
                    )
                if entry.type not in _REGULAR or entry.issparse():
                    raise Rejected(
                        f"{WORKFLOW_URL}: repository archive has an unsupported"
                        " entry among the files to attach",
                        422,
                    )
                filename = relative_path(path[len(directory) :])
                limits.check_count(len(files) + 1)
                limits.add(entry.size)
                reader = tar.extractfile(entry)
                data = reader.read() if reader is not None else b""
                files.append(Attachment(filename, data, MEDIA_TYPE))
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise GitError(f"Repository archive is invalid: {type(exc).__name__}") from None
    check_names(
        files, 422, f"{WORKFLOW_URL}: repository has files with conflicting names"
    )
    return files
