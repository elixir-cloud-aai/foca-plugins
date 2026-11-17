"""Tests of the helpers that attach fetched files to run requests."""

from typing import Any
from urllib.parse import urlsplit

from foca.plugins import Attachment, Rejected
from foca.plugins.testing import make_run_request
import pytest

from foca_plugins.wes.workflow.trs.attach import (
    FileLimits,
    attach_workflow,
    check_host,
    host_key,
    relative_path,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://TRS.Example.org/x", "trs.example.org"),
        ("https://trs.example.org:443/x", "trs.example.org"),
        ("https://trs.example.org:8443/x", "trs.example.org:8443"),
        ("https://[2001:DB8::1]:8443/x", "[2001:db8::1]:8443"),
    ],
)
def test_host_key(url: str, expected: str) -> None:
    """Hosts are lowercase, with ports other than 443.

    Args:
        url: URL.
        expected: Host.
    """
    assert host_key(urlsplit(url)) == expected


@pytest.mark.parametrize("url", ["https:///x", "https://host:99999/x"])
def test_host_key_refuses_invalid_hosts(url: str) -> None:
    """URLs without host or with an invalid port have no host key.

    Args:
        url: URL.
    """
    with pytest.raises(ValueError):
        host_key(urlsplit(url))


def test_check_host() -> None:
    """Allowed hosts pass; others are rejected with `403`."""
    check_host("a.example.org", {"a.example.org"}, "workflow_url")
    with pytest.raises(Rejected) as info:
        check_host("b.example.org", {"a.example.org"}, "workflow_url")
    assert info.value.status == 403
    assert info.value.detail == "workflow_url: host not allowed"


@pytest.mark.parametrize(
    ("path", "expected"),
    [("main.cwl", "main.cwl"), ("/tools/a.cwl", "tools/a.cwl")],
)
def test_relative_path(path: str, expected: str) -> None:
    """Leading slashes are removed.

    Args:
        path: Path.
        expected: Filename.
    """
    assert relative_path(path) == expected


@pytest.mark.parametrize("path", ["../main.cwl", "a/../../b", "", "a\\b", "C:x"])
def test_relative_path_refuses_unsafe_paths(path: str) -> None:
    """Unsafe paths are rejected with `422`, without the path.

    Args:
        path: Path.
    """
    with pytest.raises(Rejected) as info:
        relative_path(path)
    assert info.value.status == 422
    assert info.value.detail.startswith("workflow_attachment: fetched file: ")


def test_file_limits() -> None:
    """Counts and sizes up to the limits pass; more is rejected with `413`."""
    limits = FileLimits(max_files=2, max_size=10)
    limits.check_count(2)
    limits.add(6)
    limits.add(4)
    assert limits.size == 10
    with pytest.raises(Rejected) as info:
        limits.check_count(3)
    assert info.value.status == 413
    assert "more than 2 files" in info.value.detail
    with pytest.raises(Rejected) as info:
        limits.add(1)
    assert info.value.status == 413
    assert "larger than 10 bytes" in info.value.detail


def make_files(*names: str) -> list[Attachment]:
    """Build fetched files.

    Args:
        names: Filenames.

    Returns:
        Files, each with its name as content.
    """
    return [Attachment(name, name.encode(), "text/plain") for name in names]


def test_attach_workflow_adds_files_after_those_of_the_client() -> None:
    """Fetched files follow the client's; `workflow_url` names the main file."""
    params = Attachment("params.json", b"{}", "application/json")
    run: dict[str, Any] = make_run_request("trs://h/t/v", attachments=[params])
    attach_workflow(run, make_files("main.cwl", "tools/a.cwl"), "main.cwl")
    assert run["workflow_url"] == "main.cwl"
    assert [a.filename for a in run["workflow_attachment"]] == [
        "params.json",
        "main.cwl",
        "tools/a.cwl",
    ]


@pytest.mark.parametrize(
    ("attached", "fetched"),
    [
        (["Main.cwl"], ["main.cwl"]),
        (["\uff4dain.cwl"], ["main.cwl"]),  # fullwidth m
        ([], ["main.cwl", "MAIN.cwl"]),
        (["tools"], ["main.cwl", "tools/a.cwl"]),
        (["main.cwl/x"], ["main.cwl"]),
    ],
)
def test_attach_workflow_refuses_name_conflicts(
    attached: list[str], fetched: list[str]
) -> None:
    """Fetched files can't conflict with other files by name (`409`).

    Args:
        attached: Filenames of the client's files.
        fetched: Filenames of fetched files.
    """
    run: dict[str, Any] = make_run_request(
        "trs://h/t/v", attachments=make_files(*attached)
    )
    with pytest.raises(Rejected) as info:
        attach_workflow(run, make_files(*fetched), "main.cwl")
    assert info.value.status == 409
    assert run["workflow_url"] == "trs://h/t/v"
