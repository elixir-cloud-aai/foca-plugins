"""Tests of `scripts/new_plugin.py`."""

from pathlib import Path

import pytest

from scripts import new_plugin, packages

TARGET = "plugins/generic/identity/owner-tags"


def test_names() -> None:
    """The names of a plugin derive from its group, subgroup and name."""
    assert new_plugin.names("generic", "identity", "owner-tags") == {
        "plugin_id": "generic.identity.owner-tags",
        "distribution": "foca-plugin-generic-identity-owner-tags",
        "import_path": "foca_plugins.generic.identity.owner_tags",
        "directory": TARGET,
        "class_name": "OwnerTags",
    }


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        (("trs", "routing", "x"), "Group must be one of"),
        (("tes", "Routing", "x"), "Subgroups and names must match"),
        (("tes", "routing", "x_y"), "Subgroups and names must match"),
        (("tes", "routing", "-x"), "Subgroups and names must match"),
        (("tes", "routing", "distance-v2"), "must not contain"),
        (("tes", "v1", "x"), "must not contain"),
        (("generic", "example", "noop"), "That is the template"),
    ],
)
def test_validate(parts: tuple[str, str, str], message: str) -> None:
    """Unknown groups and invalid names are refused.

    Args:
        parts: Group, subgroup and name.
        message: Expected error message.
    """
    with pytest.raises(ValueError, match=message):
        new_plugin.validate(*parts)


def test_validate_version_like() -> None:
    """Only parts that look like the versions of release tags are refused.

    The patterns are those of `scripts/packages.py`.
    """
    new_plugin.validate("tes", "dev1", "ev2-x")
    assert new_plugin.VERSION_LIKE.pattern == packages.VERSION_LIKE.pattern
    assert new_plugin.NAME_PATTERN.pattern == packages.NAME_PATTERN.pattern


def test_create(repo: Path) -> None:
    """The template is copied with new names and without template-only lines.

    Args:
        repo: Repository root.
    """
    target = new_plugin.create("generic", "identity", "owner-tags", repo)
    assert target == repo / TARGET
    module = target / "src" / "foca_plugins" / "generic" / "identity" / "owner_tags"
    assert sorted(p.name for p in module.iterdir()) == ["__init__.py", "py.typed"]
    assert not (target / "src" / "foca_plugins" / "generic" / "example").exists()
    pyproject = (target / "pyproject.toml").read_text()
    assert 'name = "foca-plugin-generic-identity-owner-tags"' in pyproject
    assert '"generic.identity.owner-tags" = ' in pyproject
    assert '"foca_plugins.generic.identity.owner_tags:OwnerTags"' in pyproject
    assert 'version = "0.1.0"' in pyproject
    assert 'description = "TODO: one line on what the plugin does"' in pyproject
    assert "template only" not in pyproject
    assert "Private :: Do Not Upload" not in pyproject
    assert "tree/dev/plugins/generic/identity/owner-tags" in pyproject
    code = (module / "__init__.py").read_text()
    assert "class OwnerTags(Plugin[OwnerTagsConfig, OwnerTagsParams]):" in code
    assert "Noop" not in code
    tests = target / "tests" / "test_owner_tags.py"
    assert "from foca_plugins.generic.identity.owner_tags import" in tests.read_text()
    assert not (target / "tests" / "test_noop.py").exists()
    readme = (target / "README.md").read_text()
    assert readme.startswith("# generic.identity.owner-tags\n")
    changelog = (target / "CHANGELOG.md").read_text()
    assert "`foca-plugin-generic-identity-owner-tags-v<version>`" in changelog


def test_create_leaves_nothing_on_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while building leaves neither the package nor a temporary copy.

    Args:
        repo: Repository root.
        monkeypatch: Patches.
    """
    before = sorted(repo.rglob("*"))

    def fail(target: Path, *_: object) -> None:
        target.mkdir()
        (target / "pyproject.toml").write_text("")
        raise OSError("disk full")

    monkeypatch.setattr(new_plugin, "_build", fail)
    with pytest.raises(OSError, match="disk full"):
        new_plugin.create("generic", "identity", "owner-tags", repo)
    assert sorted(repo.rglob("*")) == before


def test_create_refuses_existing(repo: Path) -> None:
    """Existing directories are not overwritten.

    Args:
        repo: Repository root.
    """
    (repo / TARGET).mkdir(parents=True)
    with pytest.raises(FileExistsError, match="exists"):
        new_plugin.create("generic", "identity", "owner-tags", repo)


def test_main(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The command creates the plugin, or reports why it can't.

    Args:
        repo: Repository root.
        capsys: Captured output.
    """
    assert new_plugin.main(["generic", "identity", "owner-tags"], repo) == 0
    assert f"Created {TARGET}" in capsys.readouterr().out
    assert new_plugin.main(["generic", "identity", "owner-tags"], repo) == 1
    assert (
        "error: plugins/generic/identity/owner-tags exists" in capsys.readouterr().err
    )
