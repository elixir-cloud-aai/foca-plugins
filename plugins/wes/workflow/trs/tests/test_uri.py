"""Tests of the recognition of TRS URIs and of the descriptor types."""

from typing import Any

import pytest

from foca_plugins.wes.workflow.trs.trs import (
    InvalidTrsUri,
    ToolVersion,
    descriptor_type,
    parse_tool_version,
)

BASE = "https://trs.example.org/ga4gh/trs/v2"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "trs://trs.example.org/tool-1/v1",
            ToolVersion("trs.example.org", BASE, "tool-1", "v1"),
        ),
        (
            "TRS://TRS.example.org/tool-1/v1",
            ToolVersion("trs.example.org", BASE, "tool-1", "v1"),
        ),
        (
            "trs://trs.example.org:8443/tool-1/v1",
            ToolVersion(
                "trs.example.org:8443",
                "https://trs.example.org:8443/ga4gh/trs/v2",
                "tool-1",
                "v1",
            ),
        ),
        (
            "trs://dockstore.example.org/%23workflow%2Fgithub.com%2Fo%2Fr/main%201",
            ToolVersion(
                "dockstore.example.org",
                "https://dockstore.example.org/ga4gh/trs/v2",
                "#workflow/github.com/o/r",
                "main 1",
            ),
        ),
        (
            f"{BASE}/tools/tool-1/versions/v1",
            ToolVersion("trs.example.org", BASE, "tool-1", "v1"),
        ),
        (
            "https://trs.example.org:443/ga4gh/trs/v2/tools/tool-1/versions/v1/",
            ToolVersion("trs.example.org", BASE, "tool-1", "v1"),
        ),
        (
            "https://example.org/registry/a/ga4gh/trs/v2/tools/t%2F1/versions/v1",
            ToolVersion(
                "example.org",
                "https://example.org/registry/a/ga4gh/trs/v2",
                "t/1",
                "v1",
            ),
        ),
        (
            "trs://[2001:db8::1]/t/v",
            ToolVersion(
                "[2001:db8::1]", "https://[2001:db8::1]/ga4gh/trs/v2", "t", "v"
            ),
        ),
    ],
)
def test_parse_tool_version(url: str, expected: ToolVersion) -> None:
    """TRS URIs and URLs of tool versions are recognized.

    Args:
        url: `workflow_url`.
        expected: Tool version.
    """
    assert parse_tool_version(url) == expected


def test_tool_version_url_encodes_identifiers() -> None:
    """Identifiers are one encoded path segment each."""
    tool = ToolVersion("h", "https://h/ga4gh/trs/v2", "#workflow/a/b", "main 1")
    assert tool.url == (
        "https://h/ga4gh/trs/v2/tools/%23workflow%2Fa%2Fb/versions/main%201"
    )


@pytest.mark.parametrize(
    "url",
    [
        "main.cwl",
        "tools/main.cwl",
        "https://example.org/main.cwl",
        "https://example.org/ga4gh/trs/v2/tools/t/versions/v/CWL/files",
        "https://example.org/ga4gh/trs/v2/tools/t",
        "http://trs.example.org/ga4gh/trs/v2/tools/t/versions/v",
        "ftp://trs.example.org/t/v",
        "https://[::1/x",
        "",
    ],
)
def test_parse_tool_version_ignores_other_urls(url: str) -> None:
    """Other URLs and filenames are no TRS URIs.

    Args:
        url: `workflow_url`.
    """
    assert parse_tool_version(url) is None


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("trs://trs.example.org/tool", "not trs://"),
        ("trs://trs.example.org/a/b/c", "not trs://"),
        ("trs://trs.example.org/a/", "not trs://"),
        ("trs:///a/b", "no valid host"),
        ("trs://trs.example.org:99999/a/b", "no valid host"),
        ("trs://user@trs.example.org/a/b", "user info"),
        ("trs://trs.example.org/a/b?x=1", "query or fragment"),
        (f"{BASE}/tools/a/versions/b#x", "query or fragment"),
        ("trs://trs.example.org/%2E%2E/b", "'.' or '..'"),
        (f"{BASE}/tools/a/versions/..", "'.' or '..'"),
    ],
)
def test_parse_tool_version_refuses_malformed_uris(url: str, message: str) -> None:
    """Malformed TRS URIs are refused, without their values in the message.

    Args:
        url: `workflow_url`.
        message: Part of the message.
    """
    with pytest.raises(InvalidTrsUri, match=message) as info:
        parse_tool_version(url)
    assert "example.org" not in str(info.value)


@pytest.mark.parametrize(
    ("workflow_type", "expected"),
    [
        ("CWL", "CWL"),
        ("cwl", "CWL"),
        (" WDL ", "WDL"),
        ("NFL", "NFL"),
        ("Nextflow", "NFL"),
        ("SMK", "SMK"),
        ("snakemake", "SMK"),
        ("GALAXY", "GALAXY"),
        ("PYTHON", None),
        ("PLAIN_CWL", None),
        (None, None),
        (1, None),
    ],
)
def test_descriptor_type(workflow_type: Any, expected: str | None) -> None:
    """WES workflow types map to TRS descriptor types.

    Args:
        workflow_type: WES `workflow_type`.
        expected: TRS descriptor type.
    """
    assert descriptor_type(workflow_type) == expected
