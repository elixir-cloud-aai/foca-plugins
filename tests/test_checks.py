"""Tests of `scripts/checks.py`."""

from pathlib import Path

import pytest

from scripts import checks, new_plugin
from scripts.packages import NAME_PATTERN
from tests.helpers import DISTANCE, NOOP, make_wheel, replace, set_extras


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


def release(target: Path) -> None:
    """Mark the version of a new plugin (0.1.0) as released in its changelog.

    Args:
        target: Directory of the plugin.
    """
    replace(target / "CHANGELOG.md", "## Unreleased", "## 0.1.0")


def test_check_meta_pins(repo: Path) -> None:
    """Correct pins of the meta distribution pass.

    Args:
        repo: Repository root.
    """
    target = new_plugin.create("tes", "routing", "distance", repo)
    release(target)
    pin = '["foca-plugin-tes-routing-distance==0.1.0"]'
    set_extras(repo, f"tes = {pin}\ntes-routing = {pin}\nexperimental = {pin}")
    assert checks.check(repo) == []
    module = target / "src" / "foca_plugins" / "tes" / "routing" / "distance"
    replace(module / "__init__.py", 'maturity = "experimental"', 'maturity = "stable"')
    set_extras(repo, f"tes = {pin}\ntes-routing = {pin}\nall = {pin}")
    assert checks.check(repo) == []


def test_check_meta_requires_released(repo: Path) -> None:
    """Released plugins must be listed by their maturity; unreleased ones not.

    Args:
        repo: Repository root.
    """
    target = new_plugin.create("tes", "routing", "distance", repo)
    assert checks.check(repo) == []
    release(target)
    name = "foca-plugin-tes-routing-distance"
    assert checks.check(repo) == [
        f"meta: {name} is missing in [tes]",
        f"meta: {name} is missing in [tes-routing]",
        f"meta: {name} is experimental: it must be in [experimental], and not in"
        " the other of [all], [experimental]",
    ]
    pin = f'["{name}==0.1.0"]'
    set_extras(repo, f"tes = {pin}\ntes-routing = {pin}\nall = {pin}")
    assert checks.check(repo) == [
        f"meta: {name} is experimental: it must be in [experimental], and not in"
        " the other of [all], [experimental]",
    ]
    module = target / "src" / "foca_plugins" / "tes" / "routing" / "distance"
    replace(module / "__init__.py", 'maturity = "experimental"', 'maturity = "beta"')
    assert checks.check(repo) == [f"meta: {name} has an unknown maturity 'beta'"]


SOURCE = f"{DISTANCE.name} = {{ workspace = true }}"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # No entry
        (f"{SOURCE}\n", ""),
        # Another source
        (SOURCE, f'{DISTANCE.name} = {{ path = "x" }}'),
        # No table `[tool.uv.sources]`
        ("[tool.uv.sources]\n", "[tool.other]\n"),
    ],
)
def test_check_meta_sources(repo: Path, old: str, new: str) -> None:
    """Pinned plugins need a workspace source in the meta distribution.

    Args:
        repo: Repository root.
        old: Text of `meta/pyproject.toml` to replace.
        new: Replacement.
    """
    release(new_plugin.create("tes", "routing", "distance", repo))
    pin = f'["{DISTANCE.name}==0.1.0"]'
    set_extras(repo, f"tes = {pin}\ntes-routing = {pin}\nexperimental = {pin}")
    assert checks.check(repo) == []
    replace(repo / "meta" / "pyproject.toml", old, new)
    assert checks.check(repo) == [
        f"meta: {DISTANCE.name} is pinned, but [tool.uv.sources] has no `{SOURCE}`"
    ]


def test_check_meta_sources_as_uv(repo: Path) -> None:
    """Sources count as uv reads them: names normalized, other keys allowed.

    Args:
        repo: Repository root.
    """
    release(new_plugin.create("tes", "routing", "distance", repo))
    pin = f'["{DISTANCE.name}==0.1.0"]'
    set_extras(repo, f"tes = {pin}\ntes-routing = {pin}\nexperimental = {pin}")
    replace(
        repo / "meta" / "pyproject.toml",
        SOURCE,
        "Foca_Plugin_TES-routing__distance = { workspace = true, editable = false }",
    )
    assert checks.check(repo) == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('    maturity = "stable"\n', "stable"),
        ('    maturity: str = "deprecated"\n', "deprecated"),
        ("    maturity = STABLE\n    label = 'x'\n", "experimental"),
        ("    pass\n", "experimental"),
    ],
)
def test_maturity(repo: Path, code: str, expected: str) -> None:
    """The maturity is a string constant of the class, or FOCA's default.

    Args:
        repo: Repository root.
        code: Body of the plugin's class.
        expected: Maturity.
    """
    path = repo / NOOP.path / "src" / "foca_plugins" / "generic" / "example"
    (path / "noop" / "__init__.py").write_text(
        f"x = 1\n\n\nclass Other:\n    maturity = 'stable'\n\n\nclass Noop:\n{code}"
    )
    assert checks.maturity(NOOP, repo) == expected


@pytest.mark.parametrize("version", ["1.0", "1.0.0rc1", "2026.10.0.dev0", "1.0.post1"])
def test_check_versions(repo: Path, version: str) -> None:
    """Normalized PEP 440 versions pass.

    Args:
        repo: Repository root.
        version: Version.
    """
    replace(
        repo / NOOP.path / "pyproject.toml",
        'version = "0.1.0"',
        f'version = "{version}"',
    )
    assert checks.check(repo) == []


@pytest.mark.parametrize(
    "version", ["1.0.0-rc1", "1.0.0RC1", "01.0", "1!1.0", "1.0+local", "v1.0", "1.0c1"]
)
def test_check_versions_refused(repo: Path, version: str) -> None:
    """Versions that aren't normalized PEP 440 ones are reported.

    Args:
        repo: Repository root.
        version: Version.
    """
    path = repo / "meta" / "pyproject.toml"
    replace(path, 'version = "2026.10.0.dev0"', f'version = "{version}"')
    assert checks.check(repo) == [
        f"meta: version {version!r} is no normalized PEP 440 version (e.g.,"
        " `1.2.0`, `1.2.0rc1`, `1.2.0.dev0`; no epoch, no local part)"
    ]


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
    released = "is not released (no section in its CHANGELOG.md)"
    assert checks.check(repo) == [
        "meta: LICENSE must be a copy of the repository's LICENSE",
        "meta: distribution is 'x', expected 'foca-plugins'",
        f"meta: [tes] pins {name} 0.2.0, but its version is 0.1.0",
        f"meta: [tes] {name} 0.2.0 {released}",
        "meta: [tes] 'requests>=2' is no exact pin of a plugin"
        " (`<distribution>==<version>`)",
        "meta: [tes] foca-plugin-generic-example-noop is never published",
        f"meta: [all] pins {name} 0.2.0, but its version is 0.1.0",
        f"meta: [all] {name} 0.2.0 {released}",
        f"meta: [experimental] pins {name} 0.2.0, but its version is 0.1.0",
        f"meta: [experimental] {name} 0.2.0 {released}",
        f"meta: {name} is missing in [tes-routing]",
        f"meta: {name} is experimental: it must be in [experimental], and not in"
        " the other of [all], [experimental]",
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
            "foca_plugins/generic/example/noop/py.typed",
            f"{noop}.dist-info/licenses/LICENSE",
        ],
    )
    meta = "foca_plugins-2026.10.0.dev0"
    make_wheel(dist, f"{meta}-py3-none-any.whl", [f"{meta}.dist-info/licenses/LICENSE"])
    (dist / f"{meta}.tar.gz").write_bytes(b"")
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
    # As `uv build` writes without `--no-create-gitignore`
    (dist / ".gitignore").write_text("*\n")
    (dist / f"{noop}.tar.gz").write_bytes(b"")
    (dist / "other-1.0.tar.gz").write_bytes(b"")
    (dist / "build").mkdir()
    name = f"{noop}-py3-none-any.whl"
    assert checks.check_dist(dist, repo) == [
        ".gitignore: neither a wheel nor an sdist",
        "build: neither a wheel nor an sdist",
        f"{noop}.tar.gz: version is not '0.1.0'",
        "other-1.0.tar.gz: no package of this repository",
        f"{name}: version is not '0.1.0'",
        f"{name}: unexpected files: foca_plugins/__init__.py",
        f"{name}: LICENSE is missing",
        f"{name}: foca_plugins/generic/example/noop/__init__.py is missing",
        f"{name}: foca_plugins/generic/example/noop/py.typed is missing",
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
