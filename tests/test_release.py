"""Tests of `scripts/release.py`."""

import json
from pathlib import Path

import pytest

from scripts import new_plugin, release
from tests.helpers import DISTANCE, git


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
    assert release.release("foca-plugins-v0.2.0.dev0", repo)["path"] == "meta"


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v0.1.0", "is no tag"),
        ("foca-plugin-other-v0.1.0", "is no tag"),
        ("foca-plugins-0.2.0", "is no tag"),
        ("foca-plugins-v0.2.0-dev0", "is no tag"),
        ("foca-plugins-v0.3.0", "has version '0.2.0.dev0' on this commit"),
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
    tag = "foca-plugins-v0.2.0.dev0"
    with pytest.raises(ValueError, match=r"no sections '## 0\.2\.0\.dev0'"):
        release.notes(tag, repo)
    changelog.write_text("## 0.2.0.dev0\n\nA.\n\n## 0.2.0.dev0 (2)\n\nB.\n")
    with pytest.raises(ValueError, match=r"has 2 sections '## 0\.2\.0\.dev0'"):
        release.notes(tag, repo)
    changelog.write_text("# Changelog\n\n## 0.2.0.dev0\n\n## 0.1.0\n\nOld\n")
    with pytest.raises(ValueError, match="is empty"):
        release.notes(tag, repo)
    changelog.write_text(
        "# Changelog\n\n## 0.2.0.dev0 (2026-10-25)\n\nIntro.\n\n"
        "### Features\n\n- New (#1, #2)\n\n## 0.1.0\n\nOld\n"
    )
    assert release.notes(tag, repo) == "Intro.\n\n### Features\n\n- New (#1, #2)\n"
    changelog.write_text("## 0.2.0.dev0\n\nLast.\n")
    assert release.main(["notes", tag], repo) == 0
    assert capsys.readouterr().out == "Last.\n"


def test_main(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The `release` command prints the package, or why the tag is refused.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    assert release.main(["release", "foca-plugins-v0.2.0.dev0"], repo) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "foca-plugins"
    assert release.main(["release", "x"], repo) == 1
    assert "::error::'x' is no tag" in capsys.readouterr().err
    assert release.main(["previous", "foca-plugins-v0.2.0.dev0", "x"], repo) == 0
    assert capsys.readouterr().out == "\n"


def test_previous(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The previous tag is the package's newest among the commit's ancestors.

    Args:
        tmp_path: Repository.
        capsys: Captured output.
    """
    name = "foca-plugin-tes-a"
    git(tmp_path, "init", "-q")

    def commit(*tags: str) -> str:
        git(tmp_path, "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "c")
        for tag in tags:
            git(tmp_path, "tag", "--no-sign", tag)
        return git(tmp_path, "rev-parse", "HEAD")

    first = commit(f"{name}-v0.1.0")
    second = commit(f"{name}-b-v1.0.0", f"{name}-v0.2.0rc1")
    third = commit(f"{name}-v0.2.0")
    assert release.previous(f"{name}-v0.1.0", first, tmp_path) is None
    assert release.previous(f"{name}-v0.2.0rc1", second, tmp_path) == f"{name}-v0.1.0"
    assert release.previous(f"{name}-v0.2.0", third, tmp_path) == f"{name}-v0.2.0rc1"
    assert release.previous(f"{name}-b-v1.0.1", third, tmp_path) == f"{name}-b-v1.0.0"
    commit(f"{name}-v1.0-x")
    fifth = commit()
    with pytest.raises(ValueError, match="Unexpected previous tag"):
        release.previous(f"{name}-v1.0.0", fifth, tmp_path)
    assert release.main(["previous", f"{name}-v1.0.0", fifth], tmp_path) == 1
    assert "Unexpected previous tag" in capsys.readouterr().err
