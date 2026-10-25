"""Packages of the repository: discovery, change detection and mypy.

The repository holds one distribution per plugin, in
`plugins/<group>/<subgroup>/<name>/`, and the meta distribution
`foca-plugins` in `meta/` (see "Writing a plugin" in the docs). The other
scripts build on this module: `scripts/checks.py` (conventions, wheels) and
`scripts/release.py` (release tags, notes). Commands, run from the
repository root as `python -m scripts.packages <command>`:

- `changed BASE HEAD` (`.github/workflows/ci.yml`, job `changes`): the
  plugin packages affected by the changes between two commits, as the JSON
  of a CI matrix (a list of objects with `path` and `name`): changes of a
  plugin's directory affect that plugin; those of the meta distribution and
  of shared files (the root `pyproject.toml`, `uv.lock`, workflows, scripts,
  rules) affect all; those of the docs, of Markdown files outside the
  packages and of other files that no package uses affect none. Renames
  count as a deletion and an addition, so that both packages are affected.
  If `BASE` is empty, all zeros (a new branch) or unknown, all are affected.
- `mypy [PATH ...]` (`.pre-commit-config.yaml`, and `ci.yml`, job `test`):
  mypy for the given packages (directories), or for all packages, the
  scripts, their tests and the docs hooks, with the plugins' `src/`
  directories on the search path.
"""

import argparse
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = "plugins"
META = "meta"
META_NAME = "foca-plugins"
GROUPS = ("generic", "tes", "wes")
ENTRY_POINT_GROUP = "foca.plugins"
PRIVATE_CLASSIFIER = "Private :: Do Not Upload"
# Names of subgroups and plugins, as in IDs, directories and distributions;
# import paths use `_` instead of `-`
NAME_PATTERN = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
# Parts of names that look like the version of a release tag
# (`<distribution>-v<version>`), which would make tags ambiguous
VERSION_LIKE = re.compile(r"(^|-)v[0-9]")
# Changes of these affect no package: they are checked by the jobs `lint`
# and `docs` (`LICENSE` copies by `scripts/checks.py`)
UNSHARED = re.compile(
    r"(docs/.*|[^/]*\.md|plugins/([^/]+/){0,2}README\.md|mkdocs\.yml"
    r"|\.readthedocs\.yaml|CITATION\.cff|LICENSE|codecov\.yml|\.sourcery\.yaml"
    r"|\.editorconfig|\.gitignore|\.github/(CODEOWNERS|dependabot\.yml))"
)
NO_COMMIT = re.compile(r"0*")


@dataclass(frozen=True)
class Package:
    """A distribution of the repository.

    Attributes:
        path: Directory, relative to the repository root, e.g.,
            `plugins/tes/routing/distance`.
        name: Name of the distribution, e.g., `foca-plugin-tes-routing-distance`.
    """

    path: str
    name: str

    @property
    def is_plugin(self) -> bool:
        """Whether the package is a plugin (not the meta distribution).

        Returns:
            Whether it is in `plugins/`.
        """
        return self.path.startswith(f"{PLUGINS}/")

    @property
    def parts(self) -> tuple[str, str, str]:
        """Get the group, subgroup and name of a plugin.

        Returns:
            Group, subgroup and name.

        Raises:
            ValueError: The package is no plugin.
        """
        if not self.is_plugin:
            raise ValueError(f"{self.name} is no plugin")
        _, group, subgroup, name = self.path.split("/")
        return group, subgroup, name

    @property
    def plugin_id(self) -> str:
        """Get the ID of a plugin.

        Returns:
            `<group>.<subgroup>.<name>`.
        """
        return ".".join(self.parts)

    @property
    def import_path(self) -> str:
        """Get the import path of a plugin.

        Returns:
            `foca_plugins.<group>.<subgroup>.<name>`, with `_` for `-`.
        """
        return "foca_plugins." + ".".join(p.replace("-", "_") for p in self.parts)


def load_pyproject(path: Path) -> dict[str, Any]:
    """Load a `pyproject.toml` file.

    Args:
        path: File.

    Returns:
        Its content.
    """
    with path.open("rb") as file:
        return tomllib.load(file)


def discover(root: Path = ROOT) -> list[Package]:
    """Find the packages of the repository.

    Args:
        root: Repository root.

    Returns:
        The meta distribution (if any), then the plugins, by path.
    """
    files = sorted(root.glob(f"{PLUGINS}/*/*/*/pyproject.toml"))
    meta = root / META / "pyproject.toml"
    if meta.is_file():
        files.insert(0, meta)
    return [
        Package(
            path=file.parent.relative_to(root).as_posix(),
            name=load_pyproject(file)["project"]["name"],
        )
        for file in files
    ]


def plugins(packages: Iterable[Package]) -> list[Package]:
    """Select the plugins among packages.

    Args:
        packages: Packages.

    Returns:
        The plugins, in order.
    """
    return [package for package in packages if package.is_plugin]


def package_version(package: Package, root: Path) -> str:
    """Get the version of a package.

    Args:
        package: Package.
        root: Repository root.

    Returns:
        `project.version`, or an empty string if it is not static.
    """
    project = load_pyproject(root / package.path / "pyproject.toml")["project"]
    return str(project.get("version", ""))


def affected(changed: Iterable[str], packages: Sequence[Package]) -> list[Package]:
    """Find the plugins affected by changed files.

    Args:
        changed: Paths of the changed files, relative to the repository root.
        packages: Packages of the repository.

    Returns:
        Affected plugins, in the order of `packages` (cf. the module
        documentation for the rules).
    """
    candidates = plugins(packages)
    selected: set[Package] = set()
    for path in changed:
        owner = next((p for p in candidates if path.startswith(f"{p.path}/")), None)
        if owner is not None:
            selected.add(owner)
        elif UNSHARED.fullmatch(path) is None:
            return candidates
    return [package for package in candidates if package in selected]


def changed_files(base: str, head: str, root: Path = ROOT) -> list[str] | None:
    """List the files changed between two commits.

    Args:
        base: Base commit; empty or all zeros if there is none.
        head: Head commit.
        root: Repository root.

    Returns:
        Changed paths, or `None` if the base is missing or unknown.
    """
    if NO_COMMIT.fullmatch(base):
        return None
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--name-only",
            "--no-renames",
            f"{base}...{head}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, end="")
        return None
    return [line for line in result.stdout.splitlines() if line]


def mypy(paths: Sequence[str], root: Path = ROOT) -> int:
    """Run mypy.

    Args:
        paths: Directories of packages; if empty, all packages, the scripts,
            their tests and the docs hooks.
        root: Repository root.

    Returns:
        Exit status of mypy.
    """
    packages = plugins(discover(root))
    if paths:
        targets = [str(Path(path).as_posix()).rstrip("/") for path in paths]
    else:
        targets = [p.path for p in packages]
        targets += [
            p for p in ("scripts", "tests", "docs/hooks.py") if (root / p).exists()
        ]
    env = {
        **os.environ,
        "MYPYPATH": os.pathsep.join(str(root / p.path / "src") for p in packages),
    }
    return subprocess.run(
        [sys.executable, "-m", "mypy", *targets], cwd=root, env=env, check=False
    ).returncode


def matrix(packages: Iterable[Package]) -> str:
    """Format packages as the JSON of a CI matrix.

    Args:
        packages: Packages.

    Returns:
        A JSON list of objects with `path` and `name`.
    """
    return json.dumps([asdict(package) for package in packages])


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    """Run a command (cf. the module documentation).

    Args:
        argv: Arguments; defaults to those of the process.
        root: Repository root.

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    changed = commands.add_parser("changed", help="list affected plugins (JSON)")
    changed.add_argument("base", help="base commit (empty if none)")
    changed.add_argument("head", help="head commit")
    types = commands.add_parser("mypy", help="run mypy")
    types.add_argument("paths", nargs="*", help="package directories")
    args = parser.parse_args(argv)
    if args.command == "mypy":
        return mypy(args.paths, root)
    packages = discover(root)
    files = changed_files(args.base, args.head, root)
    selected = plugins(packages) if files is None else affected(files, packages)
    print(matrix(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
