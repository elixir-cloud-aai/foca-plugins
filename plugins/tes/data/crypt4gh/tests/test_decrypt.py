"""Tests of the decryption script of `tes.data.crypt4gh` (`decrypt.py`).

The script runs in the decryption image; here, it runs in the test
environment. Keys and Crypt4GH files are made for each test with the
`crypt4gh` package (a development dependency), in a temporary directory:
the repository holds no keys, so that secret scanners have nothing to
report. The last test runs the script with the arguments of the executor
that the plugin adds.
"""

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import stat
from typing import Any

from crypt4gh.keys import (  # type: ignore[import-untyped]
    c4gh,
    get_private_key,
    get_public_key,
)
from crypt4gh.lib import encrypt  # type: ignore[import-untyped]
from foca.plugins.testing import PluginHarness, make_inbound
import pytest

from foca_plugins.tes.data.crypt4gh import Crypt4gh, decrypt


@dataclass(frozen=True)
class KeyPair:
    """Key pair of a Crypt4GH user, in files.

    Attributes:
        secret: Path of the secret key.
        public: Path of the public key.
    """

    secret: Path
    public: Path


Encrypt = Callable[[Path, bytes, KeyPair], Path]
"""Encrypts data for a recipient to a file, and returns its path."""


def make_keys(directory: Path, name: str, passphrase: bytes = b"") -> KeyPair:
    """Write a key pair.

    Args:
        directory: Directory of the key files.
        name: Name of the user, the stem of the files.
        passphrase: Passphrase of the secret key; empty for none.

    Returns:
        Key pair.
    """
    directory.mkdir(parents=True, exist_ok=True)
    pair = KeyPair(secret=directory / f"{name}.sec", public=directory / f"{name}.pub")
    # `crypt4gh` 1.7 (on Python 3.15) leaves the process's umask at 0o133
    umask = os.umask(0o022)
    try:
        c4gh.generate(pair.secret, pair.public, passphrase=passphrase, comment=None)
    finally:
        os.umask(umask)
    return pair


@pytest.fixture
def alice(tmp_path: Path) -> KeyPair:
    """Create the key pair of Alice, the sender of all files.

    Args:
        tmp_path: Temporary directory of the test.

    Returns:
        Key pair.
    """
    return make_keys(tmp_path / "keys", "alice")


@pytest.fixture
def bob(tmp_path: Path) -> KeyPair:
    """Create the key pair of Bob.

    Args:
        tmp_path: Temporary directory of the test.

    Returns:
        Key pair.
    """
    return make_keys(tmp_path / "keys", "bob")


@pytest.fixture
def encrypt_file(alice: KeyPair) -> Encrypt:
    """Get a function that writes Crypt4GH files, sent by Alice.

    Args:
        alice: Key pair of the sender.

    Returns:
        Function that encrypts data for a recipient to a file.
    """
    sender = get_private_key(str(alice.secret), callback=lambda: "")

    def write(path: Path, data: bytes, recipient: KeyPair) -> Path:
        """Encrypt data for a recipient to a file.

        Args:
            path: Path of the Crypt4GH file.
            data: Data to encrypt.
            recipient: Key pair of the recipient.

        Returns:
            The path.
        """
        plain = path.with_name(f"{path.name}.plain")
        plain.write_bytes(data)
        recipient_key = get_public_key(str(recipient.public))
        with plain.open("rb") as infile, path.open("wb") as outfile:
            encrypt(keys=[(0, sender, recipient_key)], infile=infile, outfile=outfile)
        plain.unlink()
        return path

    return write


def mode(path: Path) -> int:
    """Get the permission bits of a file.

    Args:
        path: Path of the file.

    Returns:
        Permission bits.
    """
    return stat.S_IMODE(path.stat().st_mode)


def test_decrypts_files(
    tmp_path: Path, alice: KeyPair, bob: KeyPair, encrypt_file: Encrypt
) -> None:
    """Each file is decrypted with any of the keys, readable by all.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        bob: Key pair of Bob.
        encrypt_file: Writes Crypt4GH files.
    """
    one = encrypt_file(tmp_path / "one.c4gh", b"one", alice)
    two = encrypt_file(tmp_path / "two.c4gh", b"two", bob)
    out = tmp_path / "out" / "nested"
    status = decrypt.main(
        [
            *("--key", str(alice.secret), "--key", str(bob.secret)),
            *("--file", str(one), str(out / "one")),
            *("--file", str(two), str(out / "two")),
        ]
    )
    assert status == 0
    assert (out / "one").read_bytes() == b"one"
    assert (out / "two").read_bytes() == b"two"
    assert mode(out / "one") == 0o644
    assert mode(out) == 0o755
    assert sorted(p.name for p in out.iterdir()) == ["one", "two"]


def test_reads_read_only_sources(
    tmp_path: Path, alice: KeyPair, encrypt_file: Encrypt
) -> None:
    """Sources are only read: they may be read-only, and are kept.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        encrypt_file: Writes Crypt4GH files.
    """
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = encrypt_file(inputs / "data.c4gh", b"data", alice)
    source.chmod(0o444)
    inputs.chmod(0o555)
    try:
        status = decrypt.main(
            ["--key", str(alice.secret), "--file", str(source), str(tmp_path / "data")]
        )
    finally:
        inputs.chmod(0o755)
    assert status == 0
    assert (tmp_path / "data").read_bytes() == b"data"
    assert source.exists()


def test_fails_without_matching_key(
    tmp_path: Path,
    alice: KeyPair,
    bob: KeyPair,
    encrypt_file: Encrypt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A file for another key fails, and earlier targets are removed.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        bob: Key pair of Bob.
        encrypt_file: Writes Crypt4GH files.
        caplog: Captured logs.
    """
    one = encrypt_file(tmp_path / "one.c4gh", b"one", alice)
    two = encrypt_file(tmp_path / "two.c4gh", b"two", bob)
    out = tmp_path / "out"
    with caplog.at_level(logging.INFO):
        status = decrypt.main(
            [
                *("--key", str(alice.secret)),
                *("--file", str(one), str(out / "one")),
                *("--file", str(two), str(out / "two")),
            ]
        )
    assert status == 1
    assert list(out.iterdir()) == []
    assert f"Can't decrypt {two}" in caplog.text


def test_fails_on_other_files(tmp_path: Path, alice: KeyPair) -> None:
    """A file that is no Crypt4GH file fails, without leftovers.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
    """
    source = tmp_path / "plain.c4gh"
    source.write_bytes(b"not encrypted")
    out = tmp_path / "out"
    status = decrypt.main(
        ["--key", str(alice.secret), "--file", str(source), str(out / "plain")]
    )
    assert status == 1
    assert list(out.iterdir()) == []


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("missing", "No such file"),
        ("public", "Invalid key format"),
        ("passphrase", "it has a passphrase or is invalid"),
    ],
)
def test_fails_on_unusable_keys(
    tmp_path: Path,
    alice: KeyPair,
    encrypt_file: Encrypt,
    caplog: pytest.LogCaptureFixture,
    kind: str,
    reason: str,
) -> None:
    """Keys that can't be read fail before anything is written, with a reason.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        encrypt_file: Writes Crypt4GH files.
        caplog: Captured logs.
        kind: Kind of unusable key.
        reason: Part of the message.
    """
    keys = {
        "missing": tmp_path / "missing.sec",
        "public": alice.public,
        "passphrase": make_keys(tmp_path / "carol", "carol", b"passphrase").secret,
    }
    source = encrypt_file(tmp_path / "data.c4gh", b"data", alice)
    out = tmp_path / "out"
    status = decrypt.main(
        ["--key", str(keys[kind]), "--file", str(source), str(out / "data")]
    )
    assert status == 1
    assert not out.exists()
    assert "Can't read secret key" in caplog.text
    assert reason in caplog.text


def test_fails_on_unwritable_targets(
    tmp_path: Path,
    alice: KeyPair,
    encrypt_file: Encrypt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target that can't be written fails; no decrypted data is left.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        encrypt_file: Writes Crypt4GH files.
        monkeypatch: Patches `Path.replace`.
    """
    one = encrypt_file(tmp_path / "one.c4gh", b"one", alice)
    two = encrypt_file(tmp_path / "two.c4gh", b"two", alice)
    out = tmp_path / "out"
    replace = Path.replace

    def refuse_two(path: Path, target: Path) -> Path:
        """Refuse to write the target `two`.

        Args:
            path: Path of the temporary file.
            target: Target.

        Returns:
            The target.

        Raises:
            PermissionError: The target is `two`.
        """
        if Path(target).name == "two":
            raise PermissionError(os.strerror(13))
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", refuse_two)
    status = decrypt.main(
        [
            *("--key", str(alice.secret)),
            *("--file", str(one), str(out / "one")),
            *("--file", str(two), str(out / "two")),
        ]
    )
    assert status == 1
    assert list(out.iterdir()) == []


def test_fails_on_directories_that_cant_be_created(
    tmp_path: Path, alice: KeyPair, encrypt_file: Encrypt
) -> None:
    """A target below a file fails before anything is decrypted.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        encrypt_file: Writes Crypt4GH files.
    """
    source = encrypt_file(tmp_path / "data.c4gh", b"data", alice)
    (tmp_path / "file").write_text("")
    status = decrypt.main(
        ["--key", str(alice.secret), "--file", str(source), str(tmp_path / "file/x")]
    )
    assert status == 1


def test_fails_on_duplicate_targets(tmp_path: Path, alice: KeyPair) -> None:
    """Two files can't have the same target.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
    """
    target = str(tmp_path / "out" / "data")
    status = decrypt.main(
        [
            *("--key", str(alice.secret)),
            *("--file", "/in/a.c4gh", target),
            *("--file", "/in/b.c4gh", target),
        ]
    )
    assert status == 1
    assert not (tmp_path / "out").exists()


def test_keeps_permissions_of_foreign_directories(
    tmp_path: Path,
    alice: KeyPair,
    encrypt_file: Encrypt,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Directories whose permissions can't be changed are used as they are.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        encrypt_file: Writes Crypt4GH files.
        monkeypatch: Patches `Path.chmod`.
        caplog: Captured logs.
    """
    source = encrypt_file(tmp_path / "data.c4gh", b"data", alice)
    out = tmp_path / "out"
    out.mkdir()
    chmod = Path.chmod

    def refuse_directories(path: Path, path_mode: int) -> None:
        """Refuse to change the permissions of directories.

        Args:
            path: Path.
            path_mode: Permission bits.

        Raises:
            PermissionError: The path is a directory.
        """
        if path.is_dir():
            raise PermissionError(os.strerror(1))
        chmod(path, path_mode)

    monkeypatch.setattr(Path, "chmod", refuse_directories)
    status = decrypt.main(
        ["--key", str(alice.secret), "--file", str(source), str(out / "data")]
    )
    assert status == 0
    assert (out / "data").read_bytes() == b"data"
    assert f"Can't change permissions of {out}" in caplog.text


def test_needs_keys_and_files(capsys: pytest.CaptureFixture[str]) -> None:
    """Keys and files are required arguments.

    Args:
        capsys: Captured output.
    """
    with pytest.raises(SystemExit) as raised:
        decrypt.main(["--key", "/in/alice.sec"])
    assert raised.value.code == 2
    assert "--file" in capsys.readouterr().err


def test_runs_executor_of_plugin(
    tmp_path: Path, alice: KeyPair, encrypt_file: Encrypt
) -> None:
    """The executor that the plugin adds decrypts the files of the task.

    The paths of the task are those of the test's directory, so that the
    script finds them; the task's own executor gets the decrypted file.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        encrypt_file: Writes Crypt4GH files.
    """
    source = encrypt_file(tmp_path / "data.txt.c4gh", b"hello", alice)
    volume = tmp_path / "vol"
    task: dict[str, Any] = {
        "inputs": [
            {"url": "s3://bucket/data.txt.c4gh", "path": str(source)},
            {"url": "s3://bucket/alice.sec", "path": str(alice.secret)},
        ],
        "executors": [{"image": "alpine", "command": ["cat", str(source)]}],
    }
    harness = PluginHarness(
        Crypt4gh,
        config={"image": "decrypt:test", "volume": str(volume)},
        params={},
        plugin_id="tes.data.crypt4gh",
    )
    executors = harness.run_request(make_inbound(task)).payload["executors"]
    assert executors[0]["command"][0] == "crypt4gh-decrypt"
    assert decrypt.main(executors[0]["command"][1:]) == 0
    assert Path(executors[1]["command"][1]).read_bytes() == b"hello"
    assert executors[1]["command"][1] == str(volume / "data.txt")
