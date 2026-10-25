"""Fixtures of the tests of the repository's scripts."""

from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "plugins/generic/example/noop"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Copy the packages of the repository to a directory.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Root of the copy: the `LICENSE`, the meta distribution and the
        example plugin.
    """
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info", "build")
    shutil.copy(ROOT / "LICENSE", tmp_path / "LICENSE")
    shutil.copytree(ROOT / "meta", tmp_path / "meta", ignore=ignore)
    shutil.copytree(ROOT / TEMPLATE, tmp_path / TEMPLATE, ignore=ignore)
    return tmp_path
