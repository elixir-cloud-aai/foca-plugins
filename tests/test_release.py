"""Tests of `scripts/release.py`."""

import json
from pathlib import Path

import pytest

from scripts import new_plugin, release
from tests.helpers import DISTANCE


def test_release(repo: Path) -> None:
    """Tags select the package of their distribution and version.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    assert release.release("foca-plugin-tes-routing-distance-v0.1.0", repo) == {
        "path": DISTANCE.path,
        "name": DISTANCE.name,
        "version": "0.1.0",
    }
    assert release.release("foca-plugins-v2026.10.0.dev0", repo)["path"] == "meta"


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v0.1.0", "is no tag"),
        ("foca-plugin-other-v0.1.0", "is no tag"),
        ("foca-plugins-2026.10.0", "is no tag"),
        ("foca-plugins-v2026.11.0", "has version '2026.10.0.dev0' on this commit"),
        ("foca-plugin-generic-example-noop-v0.1.0", "is never published"),
    ],
)
def test_release_refused(repo: Path, tag: str, message: str) -> None:
    """Malformed tags, other versions and the example are refused.

    Args:
        repo: Repository root.
        tag: Tag.
        message: Expected error message.
    """
    with pytest.raises(ValueError, match=message):
        release.release(tag, repo)


def test_notes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The notes of a tag are its version's section of the changelog.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    changelog = repo / "meta" / "CHANGELOG.md"
    tag = "foca-plugins-v2026.10.0.dev0"
    with pytest.raises(ValueError, match=r"no section '## 2026\.10\.0\.dev0'"):
        release.notes(tag, repo)
    changelog.write_text("# Changelog\n\n## 2026.10.0.dev0\n\n## 2026.9.0\n\nOld\n")
    with pytest.raises(ValueError, match="is empty"):
        release.notes(tag, repo)
    changelog.write_text(
        "# Changelog\n\n## 2026.10.0.dev0 (2026-10-25)\n\nIntro.\n\n"
        "### Features\n\n- New (#1, #2)\n\n## 2026.9.0\n\nOld\n"
    )
    assert release.notes(tag, repo) == "Intro.\n\n### Features\n\n- New (#1, #2)\n"
    changelog.write_text("## 2026.10.0.dev0\n\nLast.\n")
    assert release.main(["notes", tag], repo) == 0
    assert capsys.readouterr().out == "Last.\n"


def test_main(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The `release` command prints the package, or why the tag is refused.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    assert release.main(["release", "foca-plugins-v2026.10.0.dev0"], repo) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "foca-plugins"
    assert release.main(["release", "x"], repo) == 1
    assert "::error::'x' is no tag" in capsys.readouterr().err
