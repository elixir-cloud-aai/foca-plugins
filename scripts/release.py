"""Releases: the package of a release tag, and its release notes.

Commands, run from the repository root as `python -m scripts.release
<command>` by `.github/workflows/release.yml`; problems are printed as
GitHub error annotations:

- `release TAG`: the package that a release tag (`<distribution>-v<version>`)
  is for, as JSON (`path`, `name`, `version`), if the version matches and
  the package may be published (not the example).
- `notes TAG`: the release notes of a tag: the section of its version in the
  package's `CHANGELOG.md`, which must be the only one of that version.
- `previous TAG COMMIT`: the package's previous release tag: the newest tag
  `<distribution>-v<version>` (normalized PEP 440 versions, pre-releases
  included) among the ancestors of the commit's parent, or nothing on its
  first release.
"""

import argparse
from collections.abc import Sequence
from dataclasses import asdict
import json
from pathlib import Path
import re
import subprocess
import sys

from scripts.packages import (
    PRIVATE_CLASSIFIER,
    ROOT,
    VERSION_PATTERN,
    discover,
    load_pyproject,
    package_version,
)


def release(tag: str, root: Path = ROOT) -> dict[str, str]:
    """Find the package of a release tag.

    Args:
        tag: Tag, `<distribution>-v<version>`.
        root: Repository root.

    Returns:
        Path, name and version of the package.

    Raises:
        ValueError: The tag is malformed, of no package or of another
            version, or the package must not be published.
    """
    name, separator, version = tag.rpartition("-v")
    package = next((p for p in discover(root) if p.name == name), None)
    if not separator or package is None or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"{tag!r} is no tag <distribution>-v<version> of a package")
    current = package_version(package, root)
    if version != current:
        raise ValueError(f"{tag!r}: {name} has version {current!r} on this commit")
    project = load_pyproject(root / package.path / "pyproject.toml")["project"]
    if PRIVATE_CLASSIFIER in project.get("classifiers", []):
        raise ValueError(f"{tag!r}: {name} is never published")
    return {**asdict(package), "version": version}


def notes(tag: str, root: Path = ROOT) -> str:
    """Get the release notes of a tag from its package's changelog.

    Args:
        tag: Tag, `<distribution>-v<version>` (cf. `release()`).
        root: Repository root.

    Returns:
        The section `## <version>` of the package's `CHANGELOG.md`, without
        its heading: the intro paragraph and the grouped sections.

    Raises:
        ValueError: The tag is refused (cf. `release()`), or the changelog
            has no section of the version, several, or an empty one.
    """
    package = release(tag, root)
    text = (root / package["path"] / "CHANGELOG.md").read_text()
    heading = re.compile(rf"^## {re.escape(package['version'])}(\s.*)?$", re.M)
    matches = list(heading.finditer(text))
    if len(matches) != 1:
        raise ValueError(
            f"{package['path']}/CHANGELOG.md has {len(matches) or 'no'} sections"
            f" '## {package['version']}', expected one"
        )
    match = matches[0]
    following = re.compile(r"^## ", re.M).search(text, match.end())
    body = text[match.end() : following.start() if following else None].strip()
    if not body:
        raise ValueError(f"The section of {package['version']} is empty")
    return body + "\n"


def previous(tag: str, commit: str, root: Path = ROOT) -> str | None:
    """Find the previous release tag of a tag's package.

    Args:
        tag: Tag, `<distribution>-v<version>`.
        commit: The tagged commit (not the tag, which may have moved).
        root: Repository root.

    Returns:
        The newest tag of the same distribution among the ancestors of the
        commit's parent, or `None` if there is none.

    Raises:
        ValueError: Git found a tag that isn't one of the distribution.
    """
    name = tag.rpartition("-v")[0]
    # Arguments are a list from repository data; no shell is involved.
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "describe",
            "--tags",
            "--abbrev=0",
            "--match",
            f"{name}-v[0-9]*",
            f"{commit}^",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    found = result.stdout.strip()
    # `--match` takes globs, which can't express versions: check exactly
    found_name, _, version = found.rpartition("-v")
    if found_name != name or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Unexpected previous tag {found!r} of {tag!r}")
    return found


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    """Run a command (cf. the module documentation).

    Args:
        argv: Arguments; defaults to those of the process.
        root: Repository root.

    Returns:
        Exit status: 1 if the tag is refused.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for command, text in (
        ("release", "find the package of a tag (JSON)"),
        ("notes", "print the notes of a tag"),
        ("previous", "print the previous tag of the package, if any"),
    ):
        subcommand = commands.add_parser(command, help=text)
        subcommand.add_argument("tag", help="<distribution>-v<version>")
        if command == "previous":
            subcommand.add_argument("commit", help="the tagged commit")
    args = parser.parse_args(argv)
    try:
        if args.command == "release":
            print(json.dumps(release(args.tag, root)))
        elif args.command == "notes":
            print(notes(args.tag, root), end="")
        else:
            print(previous(args.tag, args.commit, root) or "")
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
