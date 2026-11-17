"""Tests that the copies of modules shared by plugins stay the same.

Plugins are distributions of their own, so helpers that several plugins
need are copied into each of them rather than shared through a library
distribution. Each copy must be identical, so that a fix of one reaches
all.
"""

import pytest

from scripts.packages import ROOT

SHARED = {"attach.py": "plugins/wes/workflow/*/src/foca_plugins/wes/workflow/*"}
"""Shared modules (file names) and the pattern of the packages with a copy;
every module of that name in those packages is a copy."""


@pytest.mark.parametrize(("name", "pattern"), SHARED.items())
def test_copies_are_identical(name: str, pattern: str) -> None:
    """Every copy of a shared module has the same content.

    Args:
        name: File name of the module.
        pattern: Glob pattern of the packages with a copy.
    """
    copies = {
        path: path.read_bytes() for path in sorted(ROOT.glob(f"{pattern}/{name}"))
    }
    assert len(copies) >= 2
    first = next(iter(copies.values()))
    assert [str(p) for p, content in copies.items() if content != first] == []
