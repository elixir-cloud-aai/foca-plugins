"""Tests of `scripts/checks.py`."""

from pathlib import Path

import pytest

from scripts import checks, new_plugin
from scripts.packages import NAME_PATTERN
from tests.helpers import NOOP, make_wheel, replace, set_extras


def test_check_passes(repo: Path) -> None:
    """The template and a copy of it follow the conventions.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    assert checks.check(repo) == []


def test_check_plugin_problems(repo: Path) -> None:
    """Deviations of a plugin from the conventions are reported.

    Args:
        repo: Repository root.
    """
    target = new_plugin.create("tes", "routing", "distance", repo)
    replace(target / "pyproject.toml", 'version = "0.1.0"', 'dynamic = ["version"]')
    replace(
        target / "pyproject.toml",
        '"tes.routing.distance" =',
        '"tes.routing.other" =',
    )
    module = target / "src" / "foca_plugins" / "tes" / "routing"
    (module / "__init__.py").write_text("")
    (module / "distance" / "__init__.py").unlink()
    (target / "CHANGELOG.md").unlink()
    (target / "LICENSE").write_text("Other license\n")
    (repo / "plugins" / "wes" / "a" / "b").mkdir(parents=True)
    path = "plugins/tes/routing/distance"
    assert checks.check(repo) == [
        f"{path}: version must be static (`project.version`)",
        f"{path}: needs exactly one entry point 'tes.routing.distance' of group"
        " 'foca.plugins' referring to foca_plugins.tes.routing.distance:<class>",
        f"{path}: {path}/src/foca_plugins/tes/routing/distance/__init__.py is missing",
        f"{path}: {path}/src/foca_plugins/tes/routing must be a namespace package"
        " (no __init__.py)",
        f"{path}: CHANGELOG.md is missing",
        f"{path}: LICENSE must be a copy of the repository's LICENSE",
        "plugins/wes/a/b: no pyproject.toml (not a package)",
    ]


def test_check_plugin_names(repo: Path) -> None:
    """Unknown groups, invalid names and distribution names are reported.

    Args:
        repo: Repository root.
    """
    target = repo / "plugins" / "trs" / "Bad_Sub" / "noop"
    target.parent.mkdir(parents=True)
    (repo / NOOP.path).rename(target)
    problems = checks.check(repo)
    path = "plugins/trs/Bad_Sub/noop"
    assert problems[:3] == [
        f"{path}: group 'trs' is not one of generic, tes, wes",
        f"{path}: 'Bad_Sub' does not match {NAME_PATTERN.pattern}",
        f"{path}: distribution is 'foca-plugin-generic-example-noop', expected"
        " 'foca-plugin-trs-Bad_Sub-noop'",
    ]


def test_check_version_like_names(repo: Path) -> None:
    """Parts of names that look like the versions of release tags are reported.

    Args:
        repo: Repository root.
    """
    target = repo / "plugins" / "generic" / "v1" / "noop"
    target.parent.mkdir(parents=True)
    (repo / NOOP.path).rename(target)
    assert (
        "plugins/generic/v1/noop: 'v1' contains (^|-)v[0-9] (as in release tags)"
        in (checks.check(repo))
    )


def test_check_unique_names(repo: Path) -> None:
    """Distribution names that two plugins derive from their paths are reported.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "a-b", "c", repo)
    new_plugin.create("tes", "a", "b-c", repo)
    assert checks.check(repo) == ["distribution 'foca-plugin-tes-a-b-c' is not unique"]


def test_check_meta_pins(repo: Path) -> None:
    """Correct pins of the meta distribution pass.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    pin = '["foca-plugin-tes-routing-distance==0.1.0"]'
    set_extras(repo, f"tes = {pin}\ntes-routing = {pin}\nexperimental = {pin}")
    assert checks.check(repo) == []


def test_check_meta_problems(repo: Path) -> None:
    """Wrong pins and missing listings of the meta distribution are reported.

    Args:
        repo: Repository root.
    """
    new_plugin.create("tes", "routing", "distance", repo)
    set_extras(
        repo,
        "tes = ['foca-plugin-tes-routing-distance==0.2.0', 'requests>=2',"
        " 'foca-plugin-generic-example-noop==0.1.0']\n"
        "all = ['foca-plugin-tes-routing-distance==0.2.0']\n"
        "experimental = ['foca-plugin-tes-routing-distance==0.2.0']",
    )
    (repo / "meta" / "LICENSE").unlink()
    replace(repo / "meta" / "pyproject.toml", 'name = "foca-plugins"', 'name = "x"')
    name = "foca-plugin-tes-routing-distance"
    assert checks.check(repo) == [
        "meta: LICENSE must be a copy of the repository's LICENSE",
        "meta: distribution is 'x', expected 'foca-plugins'",
        f"meta: [tes] pins {name} 0.2.0, but its version is 0.1.0",
        "meta: [tes] 'requests>=2' is no exact pin of a plugin"
        " (`<distribution>==<version>`)",
        "meta: [tes] foca-plugin-generic-example-noop is never published",
        f"meta: [all] pins {name} 0.2.0, but its version is 0.1.0",
        f"meta: [experimental] pins {name} 0.2.0, but its version is 0.1.0",
        f"meta: {name} is missing in [tes-routing]",
        f"meta: {name} must be in exactly one of [all], [experimental]",
        "meta: foca-plugin-generic-example-noop is missing in [generic]",
        "meta: foca-plugin-generic-example-noop is missing in [generic-example]",
        "meta: foca-plugin-generic-example-noop must be in exactly one of [all],"
        " [experimental]",
    ]


def test_check_dist(repo: Path) -> None:
    """Wheels of the packages with their own files only pass.

    Args:
        repo: Repository root.
    """
    dist = repo / "dist"
    noop = "foca_plugin_generic_example_noop-0.1.0"
    make_wheel(
        dist,
        f"{noop}-py3-none-any.whl",
        [
            "foca_plugins/generic/example/noop/__init__.py",
            f"{noop}.dist-info/licenses/LICENSE",
        ],
    )
    meta = "foca_plugins-2026.10.0.dev0"
    make_wheel(dist, f"{meta}-py3-none-any.whl", [f"{meta}.dist-info/licenses/LICENSE"])
    assert checks.check_dist(dist, repo) == []


def test_check_dist_problems(repo: Path) -> None:
    """Unknown wheels, other versions and other files are reported.

    Args:
        repo: Repository root.
    """
    dist = repo / "dist"
    assert checks.check_dist(dist, repo) == [f"{dist}: no wheels"]
    noop = "foca_plugin_generic_example_noop-0.2.0"
    make_wheel(
        dist,
        f"{noop}-py3-none-any.whl",
        ["foca_plugins/__init__.py", f"{noop}.dist-info/METADATA"],
    )
    make_wheel(dist, "other-1.0-py3-none-any.whl", [])
    name = f"{noop}-py3-none-any.whl"
    assert checks.check_dist(dist, repo) == [
        f"{name}: version is not '0.1.0'",
        f"{name}: unexpected files: foca_plugins/__init__.py",
        f"{name}: LICENSE is missing",
        f"{name}: foca_plugins/generic/example/noop/__init__.py is missing",
        "other-1.0-py3-none-any.whl: no package of this repository",
    ]


def test_main(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The commands report problems as errors.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    assert checks.main(["check"], repo) == 0
    (repo / NOOP.path / "LICENSE").unlink()
    assert checks.main(["check"], repo) == 1
    assert "::error::" in capsys.readouterr().err
    assert checks.main(["dist", str(repo / "dist")], repo) == 1
    assert "no wheels" in capsys.readouterr().err
