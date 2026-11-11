"""Tests of the gateway-neutral part of `tes.data.crypt4gh` (`inputs.py`)."""

from collections.abc import Sequence

import pytest

from foca_plugins.tes.data.crypt4gh.inputs import (
    Crypt4ghInputs,
    EncryptedFile,
    InputError,
    classify,
    decrypted_name,
    within,
)


def find(paths: Sequence[str | None], field: str = "inputs") -> Crypt4ghInputs:
    """Classify inputs with the default suffixes of the plugin.

    Args:
        paths: Paths of the inputs.
        field: Name of the inputs in messages.

    Returns:
        Crypt4GH files and keys.
    """
    return classify(
        paths, encrypted_suffixes=(".c4gh",), key_suffixes=(".sec",), field=field
    )


@pytest.mark.parametrize(
    ("path", "suffixes", "expected"),
    [
        ("/in/reads.fq.c4gh", (".c4gh",), "reads.fq"),
        ("/in/reads.fq", (".c4gh",), None),
        ("/in/.c4gh", (".c4gh",), ""),
        ("/in/a.crypt4gh", (".gh", ".crypt4gh"), "a"),
        ("/in.c4gh/a", (".c4gh",), None),
    ],
)
def test_decrypted_name(
    path: str, suffixes: tuple[str, ...], expected: str | None
) -> None:
    """The longest matching suffix is removed from the file name.

    Args:
        path: Path of an input.
        suffixes: Suffixes of Crypt4GH files.
        expected: Decrypted name.
    """
    assert decrypted_name(path, suffixes) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/vol/crypt4gh", True),
        ("/vol/crypt4gh/a", True),
        ("/vol/crypt4gh2", False),
        ("/vol", False),
    ],
)
def test_within(path: str, expected: bool) -> None:
    """Paths are compared by their parts.

    Args:
        path: Path.
        expected: Whether it is in `/vol/crypt4gh`.
    """
    assert within(path, "/vol/crypt4gh") is expected


def test_classify_finds_files_and_keys() -> None:
    """Crypt4GH files and keys are found by suffix; others are skipped."""
    found = find(
        ["/in/a.c4gh", None, "/in/plain.txt", "/keys/alice.sec", "/in/b.c4gh"],
    )
    assert found == Crypt4ghInputs(
        files=(
            EncryptedFile(index=0, path="/in/a.c4gh", name="a"),
            EncryptedFile(index=4, path="/in/b.c4gh", name="b"),
        ),
        keys=("/keys/alice.sec",),
    )


def test_classify_without_crypt4gh_files() -> None:
    """Without Crypt4GH files, keys are found and nothing is required."""
    assert find(["/in/a.txt", "/k/a.sec"]) == Crypt4ghInputs(keys=("/k/a.sec",))
    assert find([]) == Crypt4ghInputs()


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        (["in/a.c4gh", "/k/a.sec"], r"^inputs\[0\]: path must be absolute$"),
        (["/in/a.c4gh", "k/a.sec"], r"^inputs\[1\]: path must be absolute$"),
        (["/k/a.sec", "/in/.c4gh"], r"^inputs\[1\]: Crypt4GH file needs a name"),
        (
            ["/in/a.c4gh", "/k/a.sec", "/other/a.c4gh"],
            r"^inputs\[0\] and inputs\[2\]: Crypt4GH files decrypt to the same",
        ),
        (["/in/a.c4gh"], r"^inputs: Crypt4GH files need a secret key .* in \.sec\)$"),
    ],
)
def test_classify_refuses(paths: list[str], message: str) -> None:
    """Inputs that can't be decrypted as they are are refused, by position.

    Args:
        paths: Paths of the inputs.
        message: Pattern of the message.
    """
    with pytest.raises(InputError, match=message):
        find(paths)


def test_classify_names_field() -> None:
    """Messages name the field of the inputs given by the caller."""
    with pytest.raises(InputError, match=r"^attachments\[0\]: path must be"):
        find(["a.c4gh"], field="attachments")
