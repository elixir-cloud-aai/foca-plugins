"""Checks of the packages: conventions and built wheels.

Commands, run from the repository root as `python -m scripts.checks
<command>`; problems are printed as GitHub error annotations:

- `check` (`.pre-commit-config.yaml`, and `.github/workflows/release.yml`):
  the conventions of the packages (unique names without parts like the
  versions of release tags, entry points, namespace packages, files,
  licenses, the pins of the meta distribution).
- `dist DIR` (`ci.yml`, job `lint`, and `release.yml`): the wheels in a
  directory (e.g., of `uv build --all-packages`): each of a package of the
  repository, of its version, with its license and only its own module (no
  files of the namespace packages).
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import re
import sys
import zipfile

from scripts.packages import (
    ENTRY_POINT_GROUP,
    GROUPS,
    META_NAME,
    NAME_PATTERN,
    PLUGINS,
    PRIVATE_CLASSIFIER,
    ROOT,
    VERSION_LIKE,
    Package,
    discover,
    load_pyproject,
    package_version,
    plugins,
)

PIN_PATTERN = re.compile(r"(?P<name>[a-z0-9-]+)==(?P<version>[^\s;,]+)")


def _check_plugin(package: Package, root: Path, license_text: str) -> list[str]:
    """Check the conventions of a plugin.

    Args:
        package: Plugin.
        root: Repository root.
        license_text: Text of the repository's `LICENSE`.

    Returns:
        Problems found.
    """
    problems: list[str] = []
    group, subgroup, name = package.parts
    if group not in GROUPS:
        problems.append(f"group {group!r} is not one of {', '.join(GROUPS)}")
    for part in (subgroup, name):
        if NAME_PATTERN.fullmatch(part) is None:
            problems.append(f"{part!r} does not match {NAME_PATTERN.pattern}")
        if VERSION_LIKE.search(part) is not None:
            problems.append(
                f"{part!r} contains {VERSION_LIKE.pattern} (as in release tags)"
            )
    expected = f"foca-plugin-{group}-{subgroup}-{name}"
    if package.name != expected:
        problems.append(f"distribution is {package.name!r}, expected {expected!r}")
    directory = root / package.path
    project = load_pyproject(directory / "pyproject.toml")["project"]
    if "version" not in project:
        problems.append("version must be static (`project.version`)")
    target = f"{package.import_path}:"
    entries = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
    if list(entries) != [package.plugin_id] or not str(
        entries[package.plugin_id]
    ).startswith(target):
        problems.append(
            f"needs exactly one entry point {package.plugin_id!r} of group"
            f" {ENTRY_POINT_GROUP!r} referring to {target}<class>"
        )
    src = directory / "src"
    module = src.joinpath(*package.import_path.split("."))
    if not (module / "__init__.py").is_file():
        problems.append(f"{module.relative_to(root)}/__init__.py is missing")
    for level in range(1, 4):
        namespace = src.joinpath(*package.import_path.split(".")[:level])
        if (namespace / "__init__.py").exists():
            problems.append(
                f"{namespace.relative_to(root)} must be a namespace package"
                " (no __init__.py)"
            )
    for required in ("README.md", "CHANGELOG.md", "tests"):
        if not (directory / required).exists():
            problems.append(f"{required} is missing")
    problems.extend(_check_license(directory, license_text))
    return problems


def _check_license(directory: Path, license_text: str) -> list[str]:
    """Check that a package ships the repository's license.

    Args:
        directory: Directory of the package.
        license_text: Text of the repository's `LICENSE`.

    Returns:
        Problems found.
    """
    path = directory / "LICENSE"
    if not path.is_file() or path.read_text() != license_text:
        return ["LICENSE must be a copy of the repository's LICENSE"]
    return []


def _check_meta(
    package: Package, packages: Sequence[Package], root: Path, license_text: str
) -> list[str]:
    """Check the meta distribution and the pins of its extras.

    Args:
        package: Meta distribution.
        packages: Packages of the repository.
        root: Repository root.
        license_text: Text of the repository's `LICENSE`.

    Returns:
        Problems found.
    """
    directory = root / package.path
    problems = _check_license(directory, license_text)
    if package.name != META_NAME:
        problems.append(f"distribution is {package.name!r}, expected {META_NAME!r}")
    extras: dict[str, list[str]] = load_pyproject(directory / "pyproject.toml")[
        "project"
    ].get("optional-dependencies", {})
    projects = {
        p.name: (p, load_pyproject(root / p.path / "pyproject.toml")["project"])
        for p in plugins(packages)
    }
    listed: dict[str, set[str]] = {}
    for extra, requirements in extras.items():
        for requirement in requirements:
            match = PIN_PATTERN.fullmatch(requirement)
            if match is None or match["name"] not in projects:
                problems.append(
                    f"[{extra}] {requirement!r} is no exact pin of a plugin"
                    " (`<distribution>==<version>`)"
                )
                continue
            plugin, project = projects[match["name"]]
            if match["version"] != project.get("version"):
                problems.append(
                    f"[{extra}] pins {plugin.name} {match['version']}, but its"
                    f" version is {project.get('version')}"
                )
            if PRIVATE_CLASSIFIER in project.get("classifiers", []):
                problems.append(f"[{extra}] {plugin.name} is never published")
            listed.setdefault(plugin.name, set()).add(extra)
    for name, names in listed.items():
        group, subgroup, _ = projects[name][0].parts
        for extra in (group, f"{group}-{subgroup}"):
            if extra not in names:
                problems.append(f"{name} is missing in [{extra}]")
        if len(names & {"all", "experimental"}) != 1:
            problems.append(f"{name} must be in exactly one of [all], [experimental]")
    return problems


def check(root: Path = ROOT) -> list[str]:
    """Check the conventions of all packages.

    Args:
        root: Repository root.

    Returns:
        Problems found, each prefixed with the package's path.
    """
    packages = discover(root)
    license_text = (root / "LICENSE").read_text()
    problems: list[str] = []
    for package in packages:
        found = (
            _check_plugin(package, root, license_text)
            if package.is_plugin
            else _check_meta(package, packages, root, license_text)
        )
        problems.extend(f"{package.path}: {problem}" for problem in found)
    names = [package.name for package in packages]
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append(f"distribution {name!r} is not unique")
    known = {package.path for package in packages}
    for directory in sorted(root.glob(f"{PLUGINS}/*/*/*/")):
        path = directory.relative_to(root).as_posix()
        if path not in known:
            problems.append(f"{path}: no pyproject.toml (not a package)")
    return problems


def _normalize(name: str) -> str:
    """Normalize a distribution name as in the names of wheels.

    Args:
        name: Name, e.g., `foca-plugin-tes-routing-distance`.

    Returns:
        Name with `_` for runs of `-`, `_` and `.`, in lowercase.
    """
    return re.sub(r"[-_.]+", "_", name).lower()


def check_dist(directory: Path, root: Path = ROOT) -> list[str]:
    """Check the wheels of a directory.

    Args:
        directory: Directory with the wheels.
        root: Repository root.

    Returns:
        Problems found, each prefixed with the wheel's file name.
    """
    known = {_normalize(package.name): package for package in discover(root)}
    wheels = sorted(directory.glob("*.whl"))
    if not wheels:
        return [f"{directory}: no wheels"]
    problems: list[str] = []
    for wheel in wheels:
        name, version = wheel.name.split("-")[:2]
        package = known.get(name)
        if package is None:
            problems.append(f"{wheel.name}: no package of this repository")
            continue
        current = package_version(package, root)
        if version != current:
            problems.append(f"{wheel.name}: version is not {current!r}")
        info = f"{name}-{version}.dist-info/"
        module = (
            package.import_path.replace(".", "/") + "/" if package.is_plugin else None
        )
        with zipfile.ZipFile(wheel) as archive:
            files = archive.namelist()
        unexpected = [
            file
            for file in files
            if not file.startswith(info)
            and (module is None or not file.startswith(module))
        ]
        if unexpected:
            problems.append(f"{wheel.name}: unexpected files: {', '.join(unexpected)}")
        if f"{info}licenses/LICENSE" not in files:
            problems.append(f"{wheel.name}: LICENSE is missing")
        if module is not None and f"{module}__init__.py" not in files:
            problems.append(f"{wheel.name}: {module}__init__.py is missing")
    return problems


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    """Run a command (cf. the module documentation).

    Args:
        argv: Arguments; defaults to those of the process.
        root: Repository root.

    Returns:
        Exit status: 1 if there are problems.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check the conventions of the packages")
    dist = commands.add_parser("dist", help="check built wheels")
    dist.add_argument("directory", type=Path, help="directory with the wheels")
    args = parser.parse_args(argv)
    if args.command == "check":
        problems = check(root)
    else:
        problems = check_dist(args.directory, root)
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
