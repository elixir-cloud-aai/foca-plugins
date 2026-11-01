"""Releases: the package of a release tag, and its release notes.

Commands, run from the repository root as `python -m scripts.release
<command>` by `.github/workflows/release.yml`; problems are printed as
GitHub error annotations:

- `release TAG`: the package that a release tag (`<distribution>-v<version>`)
  is for, as JSON (`path`, `name`, `version`), if the version matches and
  the package may be published (not the example).
- `notes TAG`: the release notes of a tag: the section of its version in the
  package's `CHANGELOG.md`, which must be the only one of that version, in
  the organization's format: an intro paragraph, then grouped sections
  (`### <name>` in the changelog, one level up as `## <name>` in the notes,
  as are deeper headings) with the names of `SECTIONS`, in that order.
- `pins TAG`: for a tag of the meta distribution, fails if a plugin version
  that its extras pin is not on PyPI yet (cf. "Releasing" in the docs); for
  plugins, does nothing.
- `previous TAG COMMIT`: the package's previous release tag: the newest tag
  `<distribution>-v<version>` (normalized PEP 440 versions, pre-releases
  included) among the ancestors of the commit's parent, or nothing on its
  first release.
"""

import argparse
from collections.abc import Sequence
from dataclasses import asdict
from http.client import HTTPException
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from scripts.packages import (
    META,
    PIN_PATTERN,
    PRIVATE_CLASSIFIER,
    ROOT,
    VERSION_PATTERN,
    discover,
    load_pyproject,
    package_version,
)

# The grouped sections of release notes, in their order (the organization's
# format); empty ones are left out
SECTIONS = (
    "Breaking changes",
    "Features",
    "Security",
    "Fixes",
    "Documentation",
    "Maintenance",
)
# Markdown (CommonMark) headings: ATX headings (up to six `#`, then a space
# or the end of the line; an optional closing sequence of `#` after a space)
# and the underlines of setext headings (`=` or `-` below a paragraph line),
# indented by up to three spaces
HEADING = re.compile(
    r" {0,3}(?P<level>#{1,6})(?:[ \t]+(?P<title>.*?)(?:[ \t]+#+)?)?[ \t]*"
)
UNDERLINE = re.compile(r" {0,3}(=+|-+)[ \t]*")
# Lines of top-level paragraph text: not blank or indented, and not of a
# list item or block quote
PARAGRAPH = re.compile(r"(?![-*+][ \t]|[0-9]+[.)][ \t]|>)\S")
# Opening fences of code blocks, whose lines are no headings (no backticks
# in the info string of a backtick fence, or it is inline code)
FENCE = re.compile(r" {0,3}(?P<fence>`{3,}(?!.*`)|~{3,})")
# A release's page in PyPI's JSON API (HTTP 404 if there is none)
PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
PYPI_TIMEOUT = 30.0


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
        its heading: the intro paragraph and the grouped sections, raised to
        `## <name>` (cf. `_format()`).

    Raises:
        ValueError: The tag is refused (cf. `release()`), the changelog has
            no section of the version, several, or an empty one, or the
            section is not in the format of release notes (cf. `_format()`).
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
    return _format(body, package["version"]) + "\n"


def _format(body: str, version: str) -> str:
    """Raise the headings of a changelog section to those of release notes.

    Args:
        body: Section of a version in a changelog, without its heading.
        version: The version, for messages.

    Returns:
        The section with its headings one level up (`### Features` becomes
        `## Features`); lines in code blocks are left as they are.

    Raises:
        ValueError: The section has no intro paragraph, a heading above level 3
            (also a setext heading), a deeper heading before the first grouped
            section, grouped sections with other names or in another order
            than `SECTIONS`, or an unclosed code block.
    """
    prefix = f"The section of {version}"
    lines: list[str] = []
    closing: re.Pattern[str] | None = None
    found: list[str] = []
    # Whether the previous line is paragraph text, which a line of `=` or `-`
    # makes a setext heading (after other lines, `---` is a thematic break)
    paragraph = False
    for line in body.splitlines():
        heading = HEADING.fullmatch(line)
        opening = FENCE.match(line)
        after_paragraph, paragraph = paragraph, False
        if closing is not None:
            if closing.fullmatch(line):
                closing = None
        elif opening is not None:
            # Closed by a line of at least as many of its characters
            fence = opening["fence"]
            closing = re.compile(
                rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*"
            )
        elif after_paragraph and UNDERLINE.fullmatch(line):
            raise ValueError(f"{prefix} has a heading {lines[-1].strip()!r}")
        elif heading is not None:
            level, title = heading["level"], heading["title"] or ""
            if not lines:
                raise ValueError(f"{prefix} has no intro paragraph")
            if len(level) < len("###"):
                raise ValueError(f"{prefix} has a heading {line.strip()!r}")
            if len(level) > len("###") and not found:
                raise ValueError(
                    f"{prefix} has a heading {line.strip()!r} outside a grouped section"
                )
            if level == "###":
                if title not in SECTIONS:
                    raise ValueError(
                        f"{prefix} has an unknown section {title!r}; expected"
                        f" one of {', '.join(SECTIONS)}"
                    )
                if found and SECTIONS.index(title) <= SECTIONS.index(found[-1]):
                    raise ValueError(
                        f"{prefix} has the section {title!r} after {found[-1]!r};"
                        f" expected the order {', '.join(SECTIONS)}"
                    )
                found.append(title)
            lines.append(line.replace("#", "", 1))
            continue
        else:
            paragraph = PARAGRAPH.match(line) is not None
        lines.append(line)
    if closing is not None:
        raise ValueError(f"{prefix} has an unclosed code block")
    return "\n".join(lines)


def unpublished(tag: str, root: Path = ROOT) -> list[str]:
    """Find the pins of a meta distribution's tag that are not on PyPI.

    Args:
        tag: Tag, `<distribution>-v<version>` (cf. `release()`).
        root: Repository root.

    Returns:
        The exact pins (`<distribution>==<version>`) of the extras whose
        versions PyPI doesn't have, in the order of the extras; empty for
        plugins, which pin nothing.

    Raises:
        ValueError: The tag is refused (cf. `release()`), or PyPI can't be
            asked (other errors than HTTP 404, which means "not on PyPI").
    """
    package = release(tag, root)
    if package["path"] != META:
        return []
    project = load_pyproject(root / META / "pyproject.toml")["project"]
    extras: dict[str, list[str]] = project.get("optional-dependencies", {})
    # Each pin once; `scripts.checks check` refuses other requirements
    pins = dict.fromkeys(
        match.group(0)
        for requirements in extras.values()
        for match in map(PIN_PATTERN.fullmatch, requirements)
        if match is not None
    )
    missing = []
    for pin in pins:
        name, _, version = pin.partition("==")
        url = PYPI_RELEASE.format(
            name=quote(name, safe=""), version=quote(version, safe="")
        )
        try:
            # A fixed HTTPS URL of PyPI, with a quoted name and version
            with urlopen(url, timeout=PYPI_TIMEOUT):  # noqa: S310
                pass
        except HTTPError as error:
            if error.code != 404:
                raise ValueError(f"Can't check {pin} on PyPI: {error}") from error
            missing.append(pin)
        # Also errors while reading the response (e.g., timeouts), which
        # `urlopen()` doesn't wrap in `URLError`
        except (OSError, HTTPException) as error:
            raise ValueError(f"Can't check {pin} on PyPI: {error}") from error
    return missing


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
        Exit status: 1 if the tag is refused, or pins are not on PyPI.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for command, text in (
        ("release", "find the package of a tag (JSON)"),
        ("notes", "print the notes of a tag"),
        ("previous", "print the previous tag of the package, if any"),
        ("pins", "fail if the meta distribution pins versions not on PyPI"),
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
        elif args.command == "pins":
            missing = unpublished(args.tag, root)
            if missing:
                raise ValueError(
                    f"Not on PyPI yet: {', '.join(missing)}. Release these plugins"
                    " first, then re-run the failed jobs"
                )
        else:
            print(previous(args.tag, args.commit, root) or "")
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
