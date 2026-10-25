"""MkDocs hooks: the plugin catalogue, generated from the plugins' READMEs.

The page with the line `<!-- catalogue -->` (`catalogue.md`) gets a table of
all plugins of the repository in its place: per plugin, its ID (linked to
its README on GitHub), its distribution, its latest release (the topmost
version section of its `CHANGELOG.md`; "not yet released" without one, and
"never" for the example, which is never published; no lookups on PyPI), and
its summary, i.e., the section `summary` of its README (between
`<!-- --8<-- [start:summary] -->` and `<!-- --8<-- [end:summary] -->`).
Plugins are found as `scripts/packages.py` finds them, so the catalogue
lists every plugin directory, and a README without a summary fails the
build.
"""

import importlib.util
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any

MARKER = "<!-- catalogue -->"
# Module name of `scripts/packages.py` when loaded by the hooks
NAME = "foca_plugins_docs_packages"
ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/elixir-cloud-aai/foca-plugins"
SUMMARY = re.compile(
    r"<!-- --8<-- \[start:summary\] -->\n(?P<text>.*?)\n"
    r"<!-- --8<-- \[end:summary\] -->",
    re.DOTALL,
)


def _load_packages() -> ModuleType:
    """Load `scripts/packages.py` (not a package) from its file.

    Returns:
        The script, as a module.
    """
    path = ROOT / "scripts" / "packages.py"
    spec = importlib.util.spec_from_file_location(NAME, path)
    if spec is None or spec.loader is None:  # pragma: no cover - file exists
        raise ImportError(f"Can't load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module  # for its dataclasses
    spec.loader.exec_module(module)
    return module


packages = _load_packages()


def summary(readme: Path) -> str:
    """Get the summary of a plugin from its README, on one line.

    Args:
        readme: README of the plugin.

    Returns:
        Text of the section `summary`, with its lines joined.

    Raises:
        ValueError: The README has no such section.
    """
    match = SUMMARY.search(readme.read_text())
    if match is None:
        raise ValueError(f"{readme} has no summary section")
    return " ".join(line.strip() for line in match["text"].splitlines()).strip()


def catalogue(root: Path = ROOT, ref: str = "dev") -> str:
    """Build the catalogue of the plugins.

    Args:
        root: Repository root.
        ref: Git ref of the links to the READMEs.

    Returns:
        A Markdown table, or a sentence if there are no plugins.
    """
    rows = []
    for package in packages.plugins(packages.discover(root)):
        project = packages.load_pyproject(root / package.path / "pyproject.toml")
        released = packages.released_versions(package, root)
        if packages.PRIVATE_CLASSIFIER in project["project"].get("classifiers", []):
            status = "never"
        else:
            status = f"`{released[0]}`" if released else "not yet released"
        text = summary(root / package.path / "README.md").replace("|", "\\|")
        rows.append(
            f"| [`{package.plugin_id}`]({REPO_URL}/tree/{ref}/{package.path}) "
            f"| `{package.name}` | {status} | {text} |"
        )
    if not rows:
        return "No plugins yet."
    header = (
        "| Plugin ID | Distribution | Latest release | Summary |\n"
        "| --- | --- | --- | --- |"
    )
    return "\n".join([header, *rows])


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    """Insert the catalogue in the page that asks for it.

    Args:
        markdown: Markdown of the page.
        page: Page (unused).
        config: MkDocs configuration (unused).
        files: Files of the site (unused).

    Returns:
        Markdown, with the catalogue in place of `MARKER`.
    """
    if MARKER not in markdown:
        return markdown
    # Read the Docs builds a commit; link to the READMEs of that commit
    ref = os.environ.get("READTHEDOCS_GIT_COMMIT_HASH", "dev")
    return markdown.replace(MARKER, catalogue(ref=ref))
