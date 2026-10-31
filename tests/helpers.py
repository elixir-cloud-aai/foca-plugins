"""Helpers of the tests of the repository's scripts."""

import os
from pathlib import Path
import re
import subprocess
import zipfile

from scripts.packages import Package

NOOP = Package(
    path="plugins/generic/example/noop", name="foca-plugin-generic-example-noop"
)
META = Package(path="meta", name="foca-plugins")
# Version of the meta distribution in the copies of the repository (fixture
# `repo`), so that the tests don't depend on the version of the next release
META_VERSION = "0.2.0.dev0"
DISTANCE = Package(
    path="plugins/tes/routing/distance", name="foca-plugin-tes-routing-distance"
)


def set_extras(repo: Path, extras: str) -> None:
    """Replace the extras of the meta distribution.

    Args:
        repo: Repository root.
        extras: TOML lines of `[project.optional-dependencies]`.
    """
    path = repo / "meta" / "pyproject.toml"
    head, _, tail = path.read_text().partition("[project.optional-dependencies]\n")
    _, _, rest = tail.partition("\n\n")
    path.write_text(f"{head}[project.optional-dependencies]\n{extras}\n\n{rest}")


def set_version(repo: Path, version: str) -> None:
    """Replace the version of the meta distribution.

    Args:
        repo: Repository root.
        version: Version.
    """
    path = repo / "meta" / "pyproject.toml"
    text, count = re.subn(
        r'^version = "[^"]*"$', f'version = "{version}"', path.read_text(), flags=re.M
    )
    assert count == 1
    path.write_text(text)


def replace(path: Path, old: str, new: str) -> None:
    """Replace a text in a file.

    Args:
        path: File.
        old: Text, which must occur.
        new: Replacement.
    """
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def git(repo: Path, *args: str) -> str:
    """Run Git in a repository, without signing or user configuration.

    Args:
        repo: Repository.
        args: Arguments.

    Returns:
        Output.
    """
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def make_wheel(directory: Path, name: str, files: list[str]) -> None:
    """Write a wheel with empty files.

    Args:
        directory: Directory.
        name: File name of the wheel.
        files: Paths in the wheel.
    """
    directory.mkdir(exist_ok=True)
    with zipfile.ZipFile(directory / name, "w") as archive:
        for file in files:
            archive.writestr(file, "")
