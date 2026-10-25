"""Tests of `scripts/packages.py`."""

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from scripts import new_plugin, packages
from scripts.packages import Package
from tests.helpers import DISTANCE, META, NOOP, git


def test_package_properties() -> None:
    """Plugins have IDs and import paths derived from their paths."""
    assert DISTANCE.is_plugin
    assert DISTANCE.parts == ("tes", "routing", "distance")
    assert DISTANCE.plugin_id == "tes.routing.distance"
    owner = Package(path="plugins/generic/identity/owner-tags", name="x")
    assert owner.import_path == "foca_plugins.generic.identity.owner_tags"
    assert not META.is_plugin
    with pytest.raises(ValueError, match="no plugin"):
        _ = META.parts


def test_discover(repo: Path) -> None:
    """The meta distribution comes first, then the plugins by path.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    assert packages.discover(repo) == [META, NOOP, DISTANCE]
    assert packages.plugins(packages.discover(repo)) == [NOOP, DISTANCE]


def test_discover_without_meta(repo: Path) -> None:
    """The meta distribution is optional.

    Args:
        repo: Repository root.
    """
    (repo / "meta" / "pyproject.toml").unlink()
    assert packages.discover(repo) == [NOOP]


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ([], []),
        (["plugins/tes/routing/distance/src/x.py"], [DISTANCE]),
        (["plugins/tes/routing/distance/README.md", "docs/index.md"], [DISTANCE]),
        (["README.md", "plugins/README.md", "plugins/tes/README.md"], []),
        (["plugins/tes/routing/README.md", "mkdocs.yml", "LICENSE"], []),
        (["meta/pyproject.toml"], [NOOP, DISTANCE]),
        (["uv.lock"], [NOOP, DISTANCE]),
        ([".github/workflows/ci.yml"], [NOOP, DISTANCE]),
        (["scripts/packages.py"], [NOOP, DISTANCE]),
        (["plugins/tes/routing/distance-v2/x.py"], [NOOP, DISTANCE]),
    ],
)
def test_affected(changed: list[str], expected: list[Package]) -> None:
    """Changed files map to the plugins that they affect.

    Args:
        changed: Changed paths.
        expected: Affected plugins.
    """
    assert packages.affected(changed, [META, NOOP, DISTANCE]) == expected


@pytest.mark.parametrize("base", ["", "0000000000000000000000000000000000000000"])
def test_changed_files_without_base(base: str) -> None:
    """Without a base commit, the changes are unknown.

    Args:
        base: Base commit.
    """
    assert packages.changed_files(base, "HEAD") is None


def test_changed_files(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The files changed between two commits are listed.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir()
    (repo / "docs" / "index.md").write_text("# Docs\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "head")
    assert packages.changed_files(base, "HEAD", repo) == ["docs/index.md"]
    assert packages.changed_files("1234567", "HEAD", repo) is None
    assert "1234567" in capsys.readouterr().err


def test_changed_files_with_moves(repo: Path) -> None:
    """A file moved between packages affects both (no rename detection).

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    tests = "tests/test_distance.py"
    git(repo, "mv", f"{DISTANCE.path}/{tests}", f"{NOOP.path}/tests/test_moved.py")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "move")
    changed = packages.changed_files(base, "HEAD", repo)
    assert changed == [f"{NOOP.path}/tests/test_moved.py", f"{DISTANCE.path}/{tests}"]
    assert packages.affected(changed, packages.discover(repo)) == [NOOP, DISTANCE]


def test_mypy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mypy checks the given packages, or all, with the `src/` directories.

    Args:
        repo: Repository root.
        monkeypatch: Patches.
    """
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args[3:], kwargs["env"]))
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(packages.subprocess, "run", run)
    (repo / "scripts").mkdir()
    assert packages.mypy([f"{NOOP.path}/"], repo) == 1
    assert packages.mypy([], repo) == 1
    assert [args for args, _ in calls] == [[NOOP.path], [NOOP.path, "scripts"]]
    assert calls[0][1]["MYPYPATH"] == str(repo / NOOP.path / "src")


def test_main(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without a known base, all plugins are affected, as a CI matrix.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    assert packages.main(["changed", "", "HEAD"], repo) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"path": NOOP.path, "name": NOOP.name}
    ]


def test_main_changed(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Known changes select the affected plugins only, possibly none.

    Args:
        repo: Repository root.
        monkeypatch: Patches.
        capsys: Captured output.
    """
    monkeypatch.setattr(packages, "changed_files", lambda *_: ["docs/index.md"])
    assert packages.main(["changed", "a", "b"], repo) == 0
    assert capsys.readouterr().out == "[]\n"
    changed = [f"{NOOP.path}/README.md"]
    monkeypatch.setattr(packages, "changed_files", lambda *_: changed)
    assert packages.main(["changed", "a", "b"], repo) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"path": NOOP.path, "name": NOOP.name}
    ]


def test_main_mypy(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The `mypy` command returns mypy's status.

    Args:
        repo: Repository root.
        monkeypatch: Patches.
    """
    calls: list[tuple[list[str], Path]] = []

    def mypy(paths: list[str], root: Path) -> int:
        calls.append((paths, root))
        return 7

    monkeypatch.setattr(packages, "mypy", mypy)
    assert packages.main(["mypy", "a", "b"], repo) == 7
    assert calls == [(["a", "b"], repo)]
