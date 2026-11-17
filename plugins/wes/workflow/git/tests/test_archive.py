"""Tests of the download and reading of repository archives.

The recorded archives in `data/` are `tar.gz` files as the hosting services
serve them, i.e., as `git archive --format=tar.gz --prefix=<top>/` writes
them (with the commit in a global pax header): `github.tar.gz`,
`gitlab.tar.gz` and `bitbucket.tar.gz` hold the same small repository of
CWL files (`README.md`, `workflows/main.cwl`, `workflows/tools/step.cwl`,
`tools/shared.cwl`), below the top directory that each service uses
(`<repo>-<ref>` for GitHub and GitLab, with `/` in refs as `-`, and
`<workspace>-<repo>-<commit>` for Bitbucket); `github-hello-world.tar.gz`
is GitHub's archive of `octocat/Hello-World` at commit `7fd1a60`, as
downloaded from `codeload.github.com`. Archives with unsafe or unusual
entries are built by the tests.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
import gzip
import io
import os
from pathlib import Path
import tarfile

from foca.plugins import Attachment, Rejected
from foca.plugins.testing import FakeHttp
from foca.utils.http import RequestNotSent
import pytest

from foca_plugins.wes.workflow.git import archive
from foca_plugins.wes.workflow.git.archive import (
    MEDIA_TYPE,
    GitError,
    download_archive,
    read_archive,
)
from foca_plugins.wes.workflow.git.attach import FileLimits
from foca_plugins.wes.workflow.git.repo import RepoFile

DATA = Path(__file__).parent / "data"
REPO = RepoFile(
    "github.com",
    "github",
    "main",
    "workflows/main.cwl",
    "https://codeload.github.com/org/workflows/tar.gz/main",
)
REPO_FILES = [
    "README.md",
    "tools/shared.cwl",
    "workflows/main.cwl",
    "workflows/tools/step.cwl",
]
"""Files of the recorded repository, in the order of its archives."""

STEP_CWL = (
    b'cwlVersion: v1.2\nclass: CommandLineTool\nbaseCommand: "true"\n'
    b"inputs: []\noutputs: []\n"
)


@dataclass(frozen=True)
class Entry:
    """Entry of an archive built by a test.

    Attributes:
        name: Name, below the top directory.
        data: Content of files.
        type: Type (`tarfile.REGTYPE`, `tarfile.DIRTYPE`, ...).
        linkname: Target of links.
        pax_headers: Pax headers.
    """

    name: str
    data: bytes = b""
    type: bytes = tarfile.REGTYPE
    linkname: str = ""
    pax_headers: dict[str, str] = field(default_factory=dict)


def make_archive(
    entries: Sequence[Entry], top: str | None = "top", fmt: int = tarfile.PAX_FORMAT
) -> bytes:
    """Build a `tar.gz` archive.

    Args:
        entries: Entries.
        top: Top directory, added first and to the names; none if `None`.
        fmt: Format of the `tar` file.

    Returns:
        Archive.
    """
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed,
        tarfile.open(
            fileobj=compressed, mode="w", format=fmt, errors="surrogateescape"
        ) as tar,
    ):
        if top is not None:
            info = tarfile.TarInfo(top)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for entry in entries:
            info = tarfile.TarInfo(f"{top}/{entry.name}" if top else entry.name)
            info.type = entry.type
            info.linkname = entry.linkname
            info.pax_headers = entry.pax_headers
            if entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.CONTTYPE):
                info.size = len(entry.data)
                tar.addfile(info, io.BytesIO(entry.data))
            else:
                tar.addfile(info)
    return buffer.getvalue()


def read(
    content: bytes,
    directory: str = "",
    max_files: int = 100,
    max_size: int = 10**6,
    max_archive_size: int = 10**7,
) -> list[Attachment]:
    """Read the files of an archive.

    Args:
        content: Archive.
        directory: Directory to read, with a trailing `/`, or `""`.
        max_files: Maximum number of files.
        max_size: Maximum total size of the files.
        max_archive_size: Maximum size of the decompressed archive.

    Returns:
        Files.
    """
    return read_archive(
        content, directory, FileLimits(max_files, max_size), max_archive_size
    )


def names(files: Sequence[Attachment]) -> list[str]:
    """Get the filenames of files.

    Args:
        files: Files.

    Returns:
        Filenames, in order.
    """
    return [file.filename for file in files]


# Recorded archives


@pytest.mark.parametrize("name", ["github", "gitlab", "bitbucket"])
def test_read_recorded_archives(name: str) -> None:
    """The archives of all three services are read alike.

    Args:
        name: Name of the recorded archive.
    """
    files = read((DATA / f"{name}.tar.gz").read_bytes())
    assert names(files) == REPO_FILES
    step = files[3]
    assert (step.content, step.media_type) == (STEP_CWL, MEDIA_TYPE)


@pytest.mark.parametrize("name", ["github", "gitlab", "bitbucket"])
def test_read_directory_of_recorded_archives(name: str) -> None:
    """Only the files of the directory are read, relative to it.

    Args:
        name: Name of the recorded archive.
    """
    files = read((DATA / f"{name}.tar.gz").read_bytes(), "workflows/")
    assert names(files) == ["main.cwl", "tools/step.cwl"]


def test_read_recorded_github_archive() -> None:
    """GitHub's own archive of a repository is read."""
    (readme,) = read((DATA / "github-hello-world.tar.gz").read_bytes())
    assert (readme.filename, readme.content) == ("README", b"Hello World!\n")


def test_read_empty_archive() -> None:
    """Archives without entries have no files."""
    assert read(make_archive([], top=None)) == []


def test_read_keeps_nested_and_binary_files() -> None:
    """Files are read as they are, with nested directories."""
    data = bytes(range(256))
    files = read(
        make_archive(
            [
                Entry("a", type=tarfile.DIRTYPE),
                Entry("a/b", type=tarfile.DIRTYPE),
                Entry("a/b/data.bin", data),
                Entry("old", b"x", type=tarfile.AREGTYPE),
            ],
            fmt=tarfile.GNU_FORMAT,
        )
    )
    assert [(f.filename, f.content) for f in files] == [
        ("a/b/data.bin", data),
        ("old", b"x"),
    ]


# Unsafe and unusual entries


@pytest.mark.parametrize(
    "entries",
    [
        [Entry("../x")],
        [Entry("a/../../x")],
        [Entry("./x")],
        [Entry("a//x")],
    ],
)
def test_read_refuses_unsafe_paths(entries: list[Entry]) -> None:
    """Entries with `..`, `.` or empty segments are refused anywhere.

    Args:
        entries: Entries.
    """
    with pytest.raises(Rejected, match="unsafe path") as info:
        read(make_archive(entries), "elsewhere/")
    assert info.value.status == 422


@pytest.mark.parametrize("name", ["/etc/passwd", "/top/x"])
def test_read_refuses_absolute_paths(name: str) -> None:
    """Absolute entries are refused, also as the first entry.

    Args:
        name: Name of the entry.
    """
    with pytest.raises(Rejected, match="unsafe path"):
        read(make_archive([Entry(name, b"x")], top=None))


@pytest.mark.parametrize(
    ("entries", "top"),
    [
        ([Entry("other/x", b"x")], None),
        ([Entry("a.cwl", b"x")], None),
        ([Entry("top", type=tarfile.DIRTYPE), Entry("x", b"x")], None),
    ],
)
def test_read_refuses_archives_without_top_directory(
    entries: list[Entry], top: str | None
) -> None:
    """All entries must be below one top directory.

    Args:
        entries: Entries.
        top: Top directory.
    """
    content = make_archive([Entry("top", type=tarfile.DIRTYPE), *entries], top)
    with pytest.raises(Rejected, match="no single top directory") as info:
        read(content)
    assert info.value.status == 422


@pytest.mark.parametrize(
    "entry",
    [
        Entry("link.cwl", type=tarfile.SYMTYPE, linkname="/etc/passwd"),
        Entry("link.cwl", type=tarfile.SYMTYPE, linkname="../../x"),
        Entry("link.cwl", type=tarfile.LNKTYPE, linkname="top/main.cwl"),
    ],
)
def test_read_refuses_links(entry: Entry) -> None:
    """Symbolic and hard links among the files to read are refused.

    Args:
        entry: Link.
    """
    content = make_archive([Entry("main.cwl", b"x"), entry])
    with pytest.raises(Rejected, match="symbolic or hard link") as info:
        read(content)
    assert info.value.status == 422


def test_read_skips_links_outside_directory() -> None:
    """Links outside the directory to read are skipped."""
    content = make_archive(
        [
            Entry("docs/link", type=tarfile.SYMTYPE, linkname="/etc/passwd"),
            Entry("w/main.cwl", b"x"),
        ]
    )
    assert names(read(content, "w/")) == ["main.cwl"]


@pytest.mark.parametrize(
    "type_", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.CONTTYPE]
)
def test_read_refuses_special_entries(type_: bytes) -> None:
    """Devices, FIFOs and contiguous files among the files are refused.

    Args:
        type_: Type of the entry.
    """
    content = make_archive([Entry("special", type=type_)])
    with pytest.raises(Rejected, match="unsupported entry") as info:
        read(content)
    assert info.value.status == 422


@pytest.mark.parametrize(
    "entry",
    [
        # GNU sparse file of the old GNU format (type `S`)
        Entry("sparse", type=tarfile.GNUTYPE_SPARSE),
        # GNU sparse file of the pax format 1.0: a map of 4 bytes of data at
        # offset 0 in a file of 1 MB, the rest a hole
        Entry(
            "GNUSparseFile.0/sparse",
            b"1\n0\n4\n".ljust(512, b"\0") + b"abcd",
            pax_headers={
                "GNU.sparse.major": "1",
                "GNU.sparse.minor": "0",
                "GNU.sparse.name": "top/sparse",
                "GNU.sparse.realsize": "1000000",
            },
        ),
    ],
)
def test_read_refuses_sparse_files(entry: Entry) -> None:
    """Sparse files, whose holes would expand when read, are refused.

    Args:
        entry: Sparse file.
    """
    fmt = tarfile.GNU_FORMAT if entry.type == tarfile.GNUTYPE_SPARSE else None
    content = make_archive([entry], fmt=fmt or tarfile.PAX_FORMAT)
    with pytest.raises(Rejected, match="unsupported entry") as info:
        read(content, max_size=10**7)
    assert info.value.status == 422


@pytest.mark.parametrize(
    "name", ["a\\b.cwl", "bad\udcff.cwl", "a\nb.cwl", "dot.", "x" * 256]
)
def test_read_refuses_unsafe_filenames(name: str) -> None:
    """Files whose names are no safe attachment filenames are refused.

    Args:
        name: Name of the file.
    """
    with pytest.raises(Rejected, match="fetched file") as info:
        read(make_archive([Entry(name, b"x")], fmt=tarfile.GNU_FORMAT))
    assert info.value.status == 422


@pytest.mark.parametrize(
    "entries",
    [
        [Entry("main.cwl", b"x"), Entry("main.cwl", b"y")],
        [Entry("Main.cwl", b"x"), Entry("main.cwl", b"y")],
        [Entry("a", b"x"), Entry("a/b", b"y")],
    ],
)
def test_read_refuses_conflicting_names(entries: list[Entry]) -> None:
    """Files can't have the same name (ignoring case), or be in another file.

    Args:
        entries: Entries.
    """
    with pytest.raises(Rejected, match="conflicting names") as info:
        read(make_archive(entries))
    assert info.value.status == 422


# Limits


def test_read_refuses_too_many_files() -> None:
    """Archives with more files than allowed are refused."""
    content = make_archive([Entry(f"{i}.cwl", b"x") for i in range(4)])
    assert len(read(content, max_files=4)) == 4
    with pytest.raises(Rejected, match="more than 3 files") as info:
        read(content, max_files=3)
    assert info.value.status == 413


def test_read_refuses_too_large_files() -> None:
    """Files larger than allowed in total are refused, before they are read."""
    content = make_archive([Entry("a", b"x" * 10), Entry("b", b"x" * 10)])
    assert len(read(content, max_size=20)) == 2
    with pytest.raises(Rejected, match="larger than 19 bytes") as info:
        read(content, max_size=19)
    assert info.value.status == 413


def test_read_counts_files_in_directory_only() -> None:
    """The limits of the files apply to the files that are read."""
    content = make_archive([Entry("big", b"x" * 100), Entry("w/main.cwl", b"x")])
    assert names(read(content, "w/", max_files=1, max_size=1)) == ["main.cwl"]


class CountingGzipFile(gzip.GzipFile):
    """`GzipFile` that counts the decompressed bytes that it returns.

    Attributes:
        total: Bytes returned by all instances.
    """

    total = 0

    def read(self, size: int | None = -1) -> bytes:
        """Read and count decompressed bytes.

        Args:
            size: Maximum number of bytes.

        Returns:
            Bytes.
        """
        data = super().read(size)
        CountingGzipFile.total += len(data)
        return data


@pytest.mark.parametrize(
    "entries",
    [
        # files that compress well (a "zip bomb"), also outside the directory
        [Entry("bomb", bytes(64 * 1024 * 1024))],
        # a pax header that compresses well, before any file
        [Entry("x", b"x", pax_headers={"comment": "x" * 8 * 1024 * 1024})],
    ],
)
def test_read_stops_at_archive_size_while_decompressing(
    monkeypatch: pytest.MonkeyPatch, entries: list[Entry]
) -> None:
    """Decompression stops at the limit, not after the whole archive.

    Args:
        monkeypatch: Pytest fixture.
        entries: Entries.
    """
    content = make_archive(entries)
    assert len(content) < 1024 * 1024
    CountingGzipFile.total = 0
    monkeypatch.setattr(archive.gzip, "GzipFile", CountingGzipFile)
    limit = 1024 * 1024
    with pytest.raises(
        Rejected, match=f"larger than {limit} bytes uncompressed"
    ) as info:
        read(content, "elsewhere/", max_size=10**9, max_archive_size=limit)
    assert info.value.status == 413
    assert CountingGzipFile.total <= limit + 1


def test_read_allows_archives_at_size_limit() -> None:
    """Archives of exactly the maximum size are read."""
    content = make_archive([Entry("a", b"x")])
    size = len(gzip.decompress(content))
    assert len(read(content, max_archive_size=size)) == 1
    with pytest.raises(Rejected, match="uncompressed"):
        read(content, max_archive_size=size - 1)


# Invalid archives


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html>not found</html>",
        gzip.compress(b"no tar file" * 100),
        make_archive([Entry("a", os.urandom(10_000))])[:2000],
        gzip.compress(b"\x00" * 100)[:-4],
    ],
)
def test_read_fails_on_invalid_archives(content: bytes) -> None:
    """Content that is no complete `tar.gz` archive fails.

    Args:
        content: Content.
    """
    with pytest.raises(GitError, match="archive is invalid"):
        read(content)


def test_read_fails_on_truncated_files() -> None:
    """Archives that end within a file fail."""
    tar = gzip.decompress(make_archive([Entry("a", b"x" * 10_000)]))
    with pytest.raises(GitError, match="archive is invalid"):
        read(gzip.compress(tar[:2048]))


def corrupt_header(tar: bytes, index: int) -> bytes:
    """Make the checksum of a header of a `tar` stream invalid.

    Args:
        tar: Uncompressed `tar` stream of entries without data, or with data
            of less than one block.
        index: Index of the header block (each header followed by one data
            block, or none).

    Returns:
        Changed stream.
    """
    start = index * tarfile.BLOCKSIZE
    return tar[: start + 148] + b"9999999\0" + tar[start + 156 :]


@pytest.mark.parametrize(
    ("tar", "message"),
    [
        # a corrupt header after the first entries, which `tarfile` takes as
        # the end of the archive
        (
            corrupt_header(
                gzip.decompress(
                    make_archive(
                        [Entry("a", b"x"), Entry("b", b"y")], fmt=tarfile.USTAR_FORMAT
                    )
                ),
                3,
            ),
            "no end of archive",
        ),
        # no zero blocks at the end
        (
            gzip.decompress(make_archive([Entry("a", b"x")], fmt=tarfile.USTAR_FORMAT))[
                : 3 * tarfile.BLOCKSIZE
            ],
            "no end of archive",
        ),
        # data after the zero blocks
        (
            gzip.decompress(make_archive([Entry("a", b"x")])) + b"garbage",
            "no end of archive",
        ),
        # malformed GNU sparse map (`tarfile` raises `ValueError`)
        (
            gzip.decompress(
                make_archive([Entry("a", b"x", pax_headers={"GNU.sparse.map": "a,b"})])
            ),
            "ValueError",
        ),
    ],
)
def test_read_fails_on_invalid_tar_streams(tar: bytes, message: str) -> None:
    """Invalid headers, missing or wrong ends of archives fail.

    Args:
        tar: Uncompressed `tar` stream.
        message: Part of the message.
    """
    with pytest.raises(GitError, match=message):
        read(gzip.compress(tar), "elsewhere/")


def test_read_refuses_long_paths() -> None:
    """Entries with names longer than Git allows are refused (`422`)."""
    content = make_archive([Entry("a/" * 2048 + "x", b"x")])
    with pytest.raises(Rejected, match="too long path") as info:
        read(content, "elsewhere/")
    assert info.value.status == 422


def test_end_check_needs_kept_bytes() -> None:
    """Ends of archives before the kept bytes can't be checked."""
    stream = archive._BoundedReader(
        io.BytesIO(bytes(200_000)).read, 10**6, Rejected("x")
    )
    stream.read(200_000)
    assert not stream.is_end(0)
    assert stream.is_end(200_000 - tarfile.BLOCKSIZE)


@pytest.mark.parametrize(
    ("rest", "expected"),
    [
        (bytes(200_000), True),
        (bytes(200_000) + b"x", False),
        (b"", False),
    ],
)
def test_end_check_reads_rest_of_stream(rest: bytes, expected: bool) -> None:
    """The rest of the stream, beyond the bytes read, must be zero blocks.

    Args:
        rest: Bytes not read yet.
        expected: Whether the stream ends there.
    """
    stream = archive._BoundedReader(io.BytesIO(b"x" + rest).read, 10**6, Rejected("x"))
    stream.read(1)
    assert stream.is_end(1) is expected


# Download


def test_download_archive() -> None:
    """The archive is downloaded from its URL."""
    http = FakeHttp()
    http.add("GET", REPO.archive_url, content=b"archive")
    assert download_archive(http, REPO) == b"archive"
    assert [call.url for call in http.calls] == [REPO.archive_url]


def test_download_archive_rejects_missing_repositories() -> None:
    """Missing (or private) repositories and refs are rejected (`422`)."""
    http = FakeHttp()
    http.add("GET", REPO.archive_url, status=404)
    with pytest.raises(Rejected, match="no such public repository or ref") as info:
        download_archive(http, REPO)
    assert info.value.status == 422


def test_download_archive_rejects_large_archives() -> None:
    """Archives larger than the HTTP client allows are rejected (`413`)."""
    http = FakeHttp(max_response_size=10)
    http.add("GET", REPO.archive_url, content=b"x" * 11)
    with pytest.raises(Rejected, match="larger than the gateway allows") as info:
        download_archive(http, REPO)
    assert info.value.status == 413


GITLAB_REPO = RepoFile(
    "gitlab.com",
    "gitlab",
    "main",
    "main.cwl",
    "https://gitlab.com/g/p/-/archive/main/archive.tar.gz",
)


@pytest.mark.parametrize(
    "location",
    [
        "https://gitlab.com/users/sign_in",
        "https://GitLab.com:443/users/sign_in",
        "/users/sign_in/",
    ],
)
def test_download_archive_rejects_missing_gitlab_projects(location: str) -> None:
    """GitLab's redirects to its sign-in page mean missing projects (`422`).

    Args:
        location: Target of the redirect.
    """
    http = FakeHttp()
    http.add("GET", GITLAB_REPO.archive_url, status=302, headers={"Location": location})
    with pytest.raises(Rejected, match="no such public repository or ref") as info:
        download_archive(http, GITLAB_REPO)
    assert info.value.status == 422


@pytest.mark.parametrize(
    ("repo", "headers"),
    [
        (GITLAB_REPO, {"Location": "https://gitlab.com/g/q/-/archive/main/a.tar.gz"}),
        (GITLAB_REPO, {"Location": "https://[::1/users/sign_in"}),
        (GITLAB_REPO, {"Location": "https://evil.example.org/users/sign_in"}),
        (GITLAB_REPO, {"Location": "https://gitlab.com:99999/users/sign_in"}),
        (GITLAB_REPO, {}),
        (REPO, {"Location": "https://github.com/users/sign_in"}),
    ],
)
def test_download_archive_fails_on_other_redirects(
    repo: RepoFile, headers: dict[str, str]
) -> None:
    """Other redirects fail, as they are not followed.

    Args:
        repo: File of the repository.
        headers: Headers of the redirect.
    """
    http = FakeHttp()
    http.add("GET", repo.archive_url, status=302, headers=headers)
    with pytest.raises(GitError, match="answered 302"):
        download_archive(http, repo)


@pytest.mark.parametrize("status", [302, 403, 500, 503])
def test_download_archive_fails_on_other_answers(status: int) -> None:
    """Other answers of the service fail.

    Args:
        status: HTTP status.
    """
    http = FakeHttp()
    http.add("GET", REPO.archive_url, status=status)
    with pytest.raises(GitError, match=f"github answered {status}"):
        download_archive(http, REPO)


def test_download_archive_fails_on_http_errors() -> None:
    """Errors of the HTTP client propagate."""
    http = FakeHttp()
    http.add("GET", REPO.archive_url, error=RequestNotSent("refused"))
    with pytest.raises(RequestNotSent):
        download_archive(http, REPO)
