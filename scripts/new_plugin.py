"""Create a plugin package from the template `generic.example.noop`.

Usage: `python scripts/new_plugin.py <group> <subgroup> <name>`, e.g.,
`python scripts/new_plugin.py tes routing distance`. It copies
`plugins/generic/example/noop/` to `plugins/<group>/<subgroup>/<name>/` and
renames the plugin ID (`<group>.<subgroup>.<name>`), the distribution
(`foca-plugin-<group>-<subgroup>-<name>`), the import path
(`foca_plugins.<group>.<subgroup>.<name>`, with `_` for `-`) and the class
(e.g., `Distance`). It removes the template-only lines (between
`# >>> template only` and `# <<< template only`, e.g., the classifier that
keeps the example off PyPI), resets the version, and writes a new README and
changelog, with TODOs. Then implement the plugin, and run `uv sync` (which
adds it to `uv.lock`) and the checks.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("generic", "tes", "wes")
NAME_PATTERN = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
# As in `scripts/packages.py`: no parts like the version of a release tag
VERSION_LIKE = re.compile(r"(^|-)v[0-9]")
TEMPLATE = ("generic", "example", "noop")
TEMPLATE_CLASS = "Noop"
TEMPLATE_BLOCK = re.compile(
    r"^[ \t]*# >>> template only.*?^[ \t]*# <<< template only[^\n]*\n",
    re.MULTILINE | re.DOTALL,
)
IGNORED = shutil.ignore_patterns(
    "__pycache__", "*.egg-info", "build", "dist", ".pytest_cache", ".mypy_cache"
)

README = """# {plugin_id}

<!-- --8<-- [start:summary] -->
TODO: what the plugin does, in a few sentences.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `{plugin_id}` |
| Distribution | `{distribution}` |
| Import path | `{import_path}` |
| APIs | TODO |
| Phases | TODO |
| Maturity | experimental |
| Data transfers | TODO: third parties that get request data, or none |
| FOCA | TODO |

## What it does

TODO: per phase, what the plugin changes, records (annotations), and when
it raises `NotApplicable`, `Rejected` or fails.

## Configuration

```yaml
plugins:
  available:
    {plugin_id}:
      distribution: {distribution}
      config: {{}}  # TODO
  pipeline:
    - id: TODO
      alternatives:
        - plugin: {plugin_id}
          params: {{}}  # TODO
```

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| TODO | | | |

## Installation

```bash
pip install {distribution}
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
"""

CHANGELOG = """# Changelog of `{distribution}`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `{distribution}-v<version>`. Each version lists its changes in the
sections of the release notes (Breaking changes, Features, Security, Fixes,
Documentation, Maintenance; empty ones left out), each entry with its issue
and pull request numbers (see "Releasing" in the docs).

## Unreleased

### Features

- Add the plugin `{plugin_id}`.
"""


def class_name(name: str) -> str:
    """Derive the name of a plugin's class from the plugin's name.

    Args:
        name: Name of the plugin, e.g., `owner-tags`.

    Returns:
        Class name, e.g., `OwnerTags`.
    """
    return "".join(part.capitalize() for part in name.split("-"))


def names(group: str, subgroup: str, name: str) -> dict[str, str]:
    """Derive the names of a plugin.

    Args:
        group: Group.
        subgroup: Subgroup.
        name: Name.

    Returns:
        Plugin ID, distribution, import path, directory and class name.
    """
    parts = (group, subgroup, name)
    return {
        "plugin_id": ".".join(parts),
        "distribution": "foca-plugin-" + "-".join(parts),
        "import_path": "foca_plugins." + ".".join(p.replace("-", "_") for p in parts),
        "directory": "plugins/" + "/".join(parts),
        "class_name": class_name(name),
    }


def validate(group: str, subgroup: str, name: str) -> None:
    """Check the parts of a plugin ID.

    Args:
        group: Group.
        subgroup: Subgroup.
        name: Name.

    Raises:
        ValueError: The group is unknown, or the subgroup or name is invalid
            or looks like a version.
    """
    if group not in GROUPS:
        raise ValueError(f"Group must be one of {', '.join(GROUPS)}: {group!r}")
    for part in (subgroup, name):
        if NAME_PATTERN.fullmatch(part) is None:
            raise ValueError(
                f"Subgroups and names must match {NAME_PATTERN.pattern}: {part!r}"
            )
        if VERSION_LIKE.search(part) is not None:
            raise ValueError(
                "Subgroups and names must not contain"
                f" {VERSION_LIKE.pattern} (release tags): {part!r}"
            )
    if (group, subgroup, name) == TEMPLATE:
        raise ValueError("That is the template")


def rename(text: str, old: dict[str, str], new: dict[str, str]) -> str:
    """Replace the names of the template in a text.

    Args:
        text: Text.
        old: Names of the template (cf. `names()`).
        new: Names of the new plugin.

    Returns:
        Text with the new names, without template-only lines.
    """
    text = TEMPLATE_BLOCK.sub("", text)
    # Longest first: the ID is part of the import path
    for key in ("distribution", "import_path", "directory", "plugin_id"):
        text = text.replace(old[key], new[key])
    # Also the prefixes of the settings models, e.g., `NoopConfig`
    return re.sub(
        rf"\b{old['class_name']}(?=Config\b|Params\b|\b)", new["class_name"], text
    )


def create(group: str, subgroup: str, name: str, root: Path = ROOT) -> Path:
    """Create a plugin package from the template.

    Args:
        group: Group.
        subgroup: Subgroup.
        name: Name.
        root: Repository root.

    Returns:
        Directory of the new package.

    Raises:
        FileExistsError: The directory exists.
        ValueError: The names are invalid (cf. `validate()`).
    """
    validate(group, subgroup, name)
    old, new = names(*TEMPLATE), names(group, subgroup, name)
    target = root / new["directory"]
    if target.exists():
        raise FileExistsError(f"{new['directory']} exists")
    shutil.copytree(root / old["directory"], target, ignore=IGNORED)
    src = target / "src"
    shutil.rmtree(src / "foca_plugins")
    module = src.joinpath(*new["import_path"].split("."))
    shutil.copytree(
        root / old["directory"] / "src" / "foca_plugins" / Path(*TEMPLATE),
        module,
        ignore=IGNORED,
    )
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".toml"}:
            path.write_text(rename(path.read_text(), old, new))
    pyproject = target / "pyproject.toml"
    text = re.sub(
        r'^version = ".*"$', 'version = "0.1.0"', pyproject.read_text(), flags=re.M
    )
    text = re.sub(
        r'^description = ".*"$',
        'description = "TODO: one line on what the plugin does"',
        text,
        flags=re.M,
    )
    pyproject.write_text(text)
    for test in (target / "tests").glob(f"test_{TEMPLATE[-1]}.py"):
        test.rename(test.with_name(f"test_{name.replace('-', '_')}.py"))
    (target / "README.md").write_text(README.format(**new))
    (target / "CHANGELOG.md").write_text(CHANGELOG.format(**new))
    return target


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    """Create a plugin package (cf. the module documentation).

    Args:
        argv: Arguments; defaults to those of the process.
        root: Repository root.

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("group", help=f"one of {', '.join(GROUPS)}")
    parser.add_argument("subgroup", help="e.g., routing")
    parser.add_argument("name", help="e.g., distance")
    args = parser.parse_args(argv)
    try:
        target = create(args.group, args.subgroup, args.name, root)
    except (FileExistsError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"Created {target.relative_to(root)}: implement the plugin, fill in the"
        " TODOs, then run `uv sync` and the checks (see `docs/writing-plugins.md`)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
