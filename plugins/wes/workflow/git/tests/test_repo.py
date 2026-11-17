"""Tests of the recognition of repository URLs and their archives."""

import pytest

from foca_plugins.wes.workflow.git.repo import (
    InvalidRepoUrl,
    RepoFile,
    parse_repo_file,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://github.com/org/workflows/blob/main/workflows/main.cwl",
            RepoFile(
                "github.com",
                "github",
                "main",
                "workflows/main.cwl",
                "https://codeload.github.com/org/workflows/tar.gz/main",
            ),
        ),
        (
            "https://GitHub.com:443/org/repo.x/blob/v1.0/main.nf",
            RepoFile(
                "github.com",
                "github",
                "v1.0",
                "main.nf",
                "https://codeload.github.com/org/repo.x/tar.gz/v1.0",
            ),
        ),
        (
            "https://github.com/org/r/blob/feature%2Fx/a%20b/main.wdl",
            RepoFile(
                "github.com",
                "github",
                "feature/x",
                "a b/main.wdl",
                "https://codeload.github.com/org/r/tar.gz/feature%2Fx",
            ),
        ),
        (
            "https://github.com/org/.github/blob/0123456789abcdef0123456789abcdef01234567/w.cwl",
            RepoFile(
                "github.com",
                "github",
                "0123456789abcdef0123456789abcdef01234567",
                "w.cwl",
                "https://codeload.github.com/org/.github/tar.gz/"
                "0123456789abcdef0123456789abcdef01234567",
            ),
        ),
        (
            "https://gitlab.com/group/project/-/blob/main/main.cwl",
            RepoFile(
                "gitlab.com",
                "gitlab",
                "main",
                "main.cwl",
                "https://gitlab.com/group/project/-/archive/main/archive.tar.gz",
            ),
        ),
        (
            "https://gitlab.com/group/sub/project/-/blob/feature%2Fx/w/main.cwl",
            RepoFile(
                "gitlab.com",
                "gitlab",
                "feature/x",
                "w/main.cwl",
                "https://gitlab.com/group/sub/project/-/archive/feature%2Fx/"
                "archive.tar.gz",
            ),
        ),
        (
            "https://git.example.org:8443/g/p/-/blob/main/main.cwl",
            RepoFile(
                "git.example.org:8443",
                "gitlab",
                "main",
                "main.cwl",
                "https://git.example.org:8443/g/p/-/archive/main/archive.tar.gz",
            ),
        ),
        (
            "https://bitbucket.org/workspace/repo/src/main/main.cwl",
            RepoFile(
                "bitbucket.org",
                "bitbucket",
                "main",
                "main.cwl",
                "https://bitbucket.org/workspace/repo/get/main.tar.gz",
            ),
        ),
        (
            "  https://bitbucket.org/w/r/src/feature%2Fx/a/b.cwl  ",
            RepoFile(
                "bitbucket.org",
                "bitbucket",
                "feature/x",
                "a/b.cwl",
                "https://bitbucket.org/w/r/get/feature%2Fx.tar.gz",
            ),
        ),
    ],
)
def test_parse_repo_file(url: str, expected: RepoFile) -> None:
    """URLs of files of the three services are recognized.

    Args:
        url: `workflow_url`.
        expected: File.
    """
    assert parse_repo_file(url, {"git.example.org:8443"}) == expected


@pytest.mark.parametrize(
    "url",
    [
        "main.cwl",
        "workflows/main.cwl",
        "trs://trs.example.org/t/v",
        "http://github.com/o/r/blob/main/main.cwl",
        "ftp://github.com/o/r/blob/main/main.cwl",
        "https://github.com/o/r",
        "https://github.com/o/r/tree/main/dir",
        "https://github.com/o/r/raw/main/main.cwl",
        "https://github.com/o/r/blob/main",
        "https://raw.githubusercontent.com/o/r/main/main.cwl",
        "https://gitlab.com/project/-/blob/main/main.cwl",
        "https://gitlab.com/g/p/-/raw/main/main.cwl",
        "https://bitbucket.org/w/r/raw/main/main.cwl",
        "https://bitbucket.org/w/r/src/main",
        "https://other.example.org/g/p/-/blob/main/main.cwl",
        "https://github.com:8443/o/r/blob/main/main.cwl",
        "https://github.com:99999/o/r/blob/main/main.cwl",
        "https:///o/r/blob/main/main.cwl",
        "https://[::1/x",
    ],
)
def test_parse_repo_file_ignores_other_urls(url: str) -> None:
    """Other URLs and other forms of the services' URLs are no repository files.

    Args:
        url: `workflow_url`.
    """
    assert parse_repo_file(url, {"git.example.org"}) is None


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://user@github.com/o/r/blob/main/w.cwl", "user info"),
        ("https://github.com/o/r/blob/main/w.cwl?plain=1", "query or fragment"),
        ("https://github.com/o/r/blob/main/w.cwl#L1", "query or fragment"),
        ("https://bitbucket.org/w/r/src/main/w.cwl?at=main", "query or fragment"),
        ("https://github.com/o%2Fx/r/blob/main/w.cwl", "owner or repository"),
        ("https://github.com/../r/blob/main/w.cwl", "owner or repository"),
        ("https://github.com/o/-/blob/main/w.cwl", "owner or repository"),
        ("https://gitlab.com/g/p%20q/-/blob/main/w.cwl", "owner or repository"),
        ("https://bitbucket.org/w/r~/src/main/w.cwl", "owner or repository"),
        ("https://github.com/o/r/blob/%2E%2E/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a..b/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a%20b/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a%00/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/%2Fa/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a%2F/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a%2F%2Fb/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a%2F.b/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/.a/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/-a/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a./w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a.lock/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a@%7Bb/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/@/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/a%3Ab/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/%FF/w.cwl", "invalid ref"),
        (f"https://github.com/o/r/blob/{'a' * 256}/w.cwl", "invalid ref"),
        ("https://github.com/o/r/blob/main/a/../w.cwl", "unsafe file path"),
        ("https://github.com/o/r/blob/main/a/%2E%2E/w.cwl", "unsafe file path"),
        ("https://github.com/o/r/blob/main/dir/", "unsafe file path"),
        ("https://github.com/o/r/blob/main/a//w.cwl", "unsafe file path"),
        ("https://github.com/o/r/blob/main/a%5Cw.cwl", "unsafe file path"),
        ("https://github.com/o/r/blob/main/%FF.cwl", "unsafe file path"),
        ("https://github.com/o/r/blob/main/a%0Ab.cwl", "unsafe file path"),
    ],
)
def test_parse_repo_file_refuses_malformed_urls(url: str, message: str) -> None:
    """URLs of the services' forms with invalid parts are malformed.

    Args:
        url: `workflow_url`.
        message: Part of the expected message.
    """
    with pytest.raises(InvalidRepoUrl, match=message):
        parse_repo_file(url)


def test_parse_repo_file_accepts_long_valid_refs() -> None:
    """Refs of up to 255 bytes are valid."""
    ref = "a" * 255
    repo = parse_repo_file(f"https://github.com/o/r/blob/{ref}/w.cwl")
    assert repo is not None
    assert repo.ref == ref
