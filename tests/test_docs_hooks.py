"""Tests of the MkDocs hooks (`docs/hooks.py`)."""

from pathlib import Path

import pytest

from docs import hooks
from scripts import new_plugin

URL = "https://github.com/elixir-cloud-aai/foca-plugins/tree"


def test_summary(tmp_path: Path) -> None:
    """The summary section is joined to one line; it must exist.

    Args:
        tmp_path: Temporary directory.
    """
    readme = tmp_path / "README.md"
    readme.write_text(
        "# x\n\n<!-- --8<-- [start:summary] -->\nOne\n  two.\n"
        "<!-- --8<-- [end:summary] -->\n"
    )
    assert hooks.summary(readme) == "One two."
    readme.write_text("# x\n")
    with pytest.raises(ValueError, match="no summary section"):
        hooks.summary(readme)


def test_catalogue(repo: Path) -> None:
    """Every plugin gets a row, with its README's summary.

    Args:
        repo: Repository root.
    """
    target = new_plugin.create("tes", "routing", "distance", repo)
    readme = target / "README.md"
    readme.write_text(readme.read_text().replace("TODO: what", "A | B: what"))
    rows = hooks.catalogue(repo, ref="abc").splitlines()
    assert rows[0] == "| Plugin ID | Distribution | Published | Summary |"
    assert rows[2].startswith(
        f"| [`generic.example.noop`]({URL}/abc/plugins/generic/example/noop)"
        " | `foca-plugin-generic-example-noop` | no | Example plugin that"
    )
    assert rows[3] == (
        f"| [`tes.routing.distance`]({URL}/abc/plugins/tes/routing/distance)"
        " | `foca-plugin-tes-routing-distance` | yes"
        " | A \\| B: what the plugin does, in a few sentences. |"
    )


def test_catalogue_without_plugins(tmp_path: Path) -> None:
    """Without plugins, the catalogue says so.

    Args:
        tmp_path: Temporary directory.
    """
    assert hooks.catalogue(tmp_path) == "No plugins yet."


def test_on_page_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the marker is replaced, with links to the built commit.

    Args:
        monkeypatch: Patches.
    """
    assert hooks.on_page_markdown("# Page\n", None, None, None) == "# Page\n"
    monkeypatch.setenv("READTHEDOCS_GIT_COMMIT_HASH", "1234")
    page = hooks.on_page_markdown(f"# Catalogue\n\n{hooks.MARKER}\n", None, None, None)
    assert f"{URL}/1234/plugins/generic/example/noop" in page
    assert hooks.MARKER not in page
