"""Tests that the copies of modules shared by plugins stay the same.

Plugins are distributions of their own, so helpers that several plugins
need are copied into each of them rather than shared through a library
distribution. Each copy must be identical, so that a fix of one reaches
all.
"""

from pathlib import Path

import pytest

from scripts.packages import ROOT

SHARED = {
    "attach.py": [
        "plugins/wes/workflow/trs/src/foca_plugins/wes/workflow/trs",
        "plugins/wes/workflow/git/src/foca_plugins/wes/workflow/git",
    ],
}
"""Shared modules (file names) and the package directories with a copy."""


@pytest.mark.parametrize(("name", "packages"), SHARED.items())
def test_copies_are_identical(name: str, packages: list[str]) -> None:
    """Every copy of a shared module has the same content.

    Args:
        name: File name of the module.
        packages: Directories of the packages with a copy.
    """
    copies = {
        package: (Path(ROOT) / package / name).read_bytes() for package in packages
    }
    first = copies[packages[0]]
    assert [p for p, content in copies.items() if content != first] == []
