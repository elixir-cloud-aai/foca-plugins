"""Tests of the decryption script of `tes.data.crypt4gh` (`decrypt.py`).

The script runs in the decryption image; here, it runs in the test
environment. Keys and Crypt4GH files are made for each test with the
`crypt4gh` package (a development dependency), in a temporary directory:
the repository holds no keys, so that secret scanners have nothing to
report. The last test runs the script with the arguments of the decryption
and teardown executors that the plugin adds.
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


def test_fails_on_files_that_cant_be_removed(
    tmp_path: Path,
    alice: KeyPair,
    bob: KeyPair,
    encrypt_file: Encrypt,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Targets that can't be removed after a failure are logged; status 1.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        bob: Key pair of Bob.
        encrypt_file: Writes Crypt4GH files.
        monkeypatch: Patches `Path.unlink`.
        caplog: Captured logs.
    """
    one = encrypt_file(tmp_path / "one.c4gh", b"one", alice)
    two = encrypt_file(tmp_path / "two.c4gh", b"two", bob)
    out = tmp_path / "out"
    unlink = Path.unlink

    def refuse_one(path: Path, missing_ok: bool = False) -> None:
        """Refuse to remove the target `one`.

        Args:
            path: Path.
            missing_ok: Whether a missing file is fine.

        Raises:
            PermissionError: The path is the target `one`.
        """
        if path.name == "one":
            raise PermissionError(os.strerror(13))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_one)
    status = decrypt.main(
        [
            *("--key", str(alice.secret)),
            *("--file", str(one), str(out / "one")),
            *("--file", str(two), str(out / "two")),
        ]
    )
    assert status == 1
    assert f"Can't remove {out / 'one'}" in caplog.text


def test_fails_on_temporary_files_that_cant_be_removed(
    tmp_path: Path,
    alice: KeyPair,
    bob: KeyPair,
    encrypt_file: Encrypt,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A temporary file that can't be removed doesn't hide the error.

    The earlier targets are removed all the same, and the script exits
    with status 1.

    Args:
        tmp_path: Temporary directory of the test.
        alice: Key pair of Alice.
        bob: Key pair of Bob.
        encrypt_file: Writes Crypt4GH files.
        monkeypatch: Patches `Path.unlink`.
        caplog: Captured logs.
    """
    one = encrypt_file(tmp_path / "one.c4gh", b"one", alice)
    two = encrypt_file(tmp_path / "two.c4gh", b"two", bob)
    out = tmp_path / "out"
    unlink = Path.unlink

    def refuse_temporary(path: Path, missing_ok: bool = False) -> None:
        """Refuse to remove temporary files.

        Args:
            path: Path.
            missing_ok: Whether a missing file is fine.

        Raises:
            PermissionError: The path is a temporary file.
        """
        if path.name.startswith("."):
            raise PermissionError(os.strerror(13))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_temporary)
    status = decrypt.main(
        [
            *("--key", str(alice.secret)),
            *("--file", str(one), str(out / "one")),
            *("--file", str(two), str(out / "two")),
        ]
    )
    assert status == 1
    assert not (out / "one").exists()
    assert f"Can't decrypt {two}" in caplog.text
    assert "Can't remove temporary file" in caplog.text


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


@pytest.mark.parametrize(
    ("argv", "missing"),
    [
        (["--key", "/in/alice.sec"], "--file"),
        (["--file", "/in/a.c4gh", "/vol/a"], "--key"),
        ([], "--key, --file"),
    ],
)
def test_needs_keys_and_files(
    argv: list[str], missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keys and files are required arguments, unless a directory is cleared.

    Args:
        argv: Arguments.
        missing: Option that the error names.
        capsys: Captured output.
    """
    with pytest.raises(SystemExit) as raised:
        decrypt.main(argv)
    assert raised.value.code == 2
    assert missing in capsys.readouterr().err


def test_clears_directory(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The teardown removes everything in the directory, but keeps it.

    Files, temporary files, directories and symbolic links are removed;
    links are not followed. A missing directory is fine, so that the
    teardown may run again.

    Args:
        tmp_path: Temporary directory of the test.
        caplog: Captured logs.
    """
    volume = tmp_path / "vol"
    outside = tmp_path / "outside"
    (volume / "sub").mkdir(parents=True)
    outside.mkdir()
    (outside / "keep").write_bytes(b"keep")
    for name in ("one", ".one.x1y2", "one.fai", "sub/derived"):
        (volume / name).write_bytes(b"data")
    (volume / "link").symlink_to(outside)
    (volume / "file-link").symlink_to(outside / "keep")
    caplog.set_level(logging.INFO, logger="crypt4gh-decrypt")
    assert decrypt.main(["--clear", str(volume)]) == 0
    assert volume.is_dir()
    assert list(volume.iterdir()) == []
    assert (outside / "keep").read_bytes() == b"keep"
    assert f"Removed {volume / 'one'}" in caplog.text
    assert decrypt.main(["--clear", str(volume)]) == 0
    assert decrypt.main(["--clear", str(tmp_path / "gone")]) == 0


def test_fails_on_entries_that_cant_be_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Entries that the teardown can't remove are logged; status 1.

    The other entries are removed all the same.

    Args:
        tmp_path: Temporary directory of the test.
        monkeypatch: Patches `Path.unlink`.
        caplog: Captured logs.
    """
    volume = tmp_path / "vol"
    volume.mkdir()
    for name in ("one", "two"):
        (volume / name).write_bytes(b"data")
    unlink = Path.unlink

    def refuse_one(path: Path, missing_ok: bool = False) -> None:
        """Refuse to remove the entry `one`.

        Args:
            path: Path.
            missing_ok: Whether a missing file is fine.

        Raises:
            PermissionError: The path is the entry `one`.
        """
        if path.name == "one":
            raise PermissionError(os.strerror(13))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_one)
    assert decrypt.main(["--clear", str(volume)]) == 1
    assert [path.name for path in volume.iterdir()] == ["one"]
    assert f"Can't remove {volume / 'one'}" in caplog.text


def test_goes_on_in_directories_after_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An entry of a subdirectory that can't be removed doesn't stop the rest.

    Its siblings and the other entries of the volume are removed; the
    subdirectory stays with that entry; status 1.

    Args:
        tmp_path: Temporary directory of the test.
        monkeypatch: Patches `Path.unlink`.
        caplog: Captured logs.
    """
    volume = tmp_path / "vol"
    (volume / "sub" / "deeper").mkdir(parents=True)
    for name in ("a", "bad", "z", "deeper/x"):
        (volume / "sub" / name).write_bytes(b"data")
    (volume / "other").write_bytes(b"data")
    unlink = Path.unlink

    def refuse_bad(path: Path, missing_ok: bool = False) -> None:
        """Refuse to remove the entry `bad`.

        Args:
            path: Path.
            missing_ok: Whether a missing file is fine.

        Raises:
            PermissionError: The path is the entry `bad`.
        """
        if path.name == "bad":
            raise PermissionError(os.strerror(13))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_bad)
    assert decrypt.main(["--clear", str(volume)]) == 1
    assert [str(path.relative_to(volume)) for path in volume.rglob("*")] == [
        "sub",
        "sub/bad",
    ]
    assert f"Can't remove {volume / 'sub' / 'bad'}" in caplog.text


def test_skips_entries_removed_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry that is gone when the teardown removes it is fine.

    Args:
        tmp_path: Temporary directory of the test.
        monkeypatch: Patches `Path.unlink`.
    """
    volume = tmp_path / "vol"
    volume.mkdir()
    (volume / "one").write_bytes(b"data")

    def gone(path: Path, missing_ok: bool = False) -> None:
        """Act as if the entry was removed by someone else.

        Args:
            path: Path.
            missing_ok: Whether a missing file is fine.

        Raises:
            FileNotFoundError: Always.
        """
        raise FileNotFoundError(os.strerror(2))

    monkeypatch.setattr(Path, "unlink", gone)
    assert decrypt.main(["--clear", str(volume)]) == 0


def test_fails_on_directories_that_cant_be_listed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A directory that can't be listed fails the teardown.

    Args:
        tmp_path: Temporary directory of the test.
        monkeypatch: Patches `Path.iterdir`.
        caplog: Captured logs.
    """

    def refuse(path: Path) -> None:
        """Refuse to list directories.

        Args:
            path: Path.

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(os.strerror(13))

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert decrypt.main(["--clear", str(tmp_path)]) == 1
    assert f"Can't list {tmp_path}" in caplog.text


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--clear", "/vol/a", "--key", "/in/alice.sec"], "can't be combined"),
        (["--clear", "/vol/a", "--file", "/in/a.c4gh", "/vol/a"], "can't be combined"),
        (["--clear", "vol/a"], "absolute path other than /"),
        (["--clear", "/"], "absolute path other than /"),
        (["--clear", "//"], "absolute path other than /"),
        (["--clear", "/vol/.."], "absolute path other than /"),
    ],
)
def test_refuses_bad_clear_arguments(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--clear` needs an absolute path other than `/`, and no keys or files.

    Args:
        argv: Arguments.
        message: Part of the expected error.
        capsys: Captured output.
    """
    with pytest.raises(SystemExit) as raised:
        decrypt.main(argv)
    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "--clear" in error
    assert message in error


def test_refuses_to_clear_links(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory that is a symbolic link isn't followed, so not emptied.

    Args:
        tmp_path: Temporary directory of the test.
        capsys: Captured output.
    """
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "keep").write_bytes(b"keep")
    (tmp_path / "vol").symlink_to(tmp_path / "data")
    with pytest.raises(SystemExit) as raised:
        decrypt.main(["--clear", str(tmp_path / "vol")])
    assert raised.value.code == 2
    assert "not a symbolic link" in capsys.readouterr().err
    assert (tmp_path / "data" / "keep").exists()


def test_runs_executor_of_plugin(
    tmp_path: Path, alice: KeyPair, encrypt_file: Encrypt
) -> None:
    """The executors that the plugin adds decrypt and remove the files.

    The paths of the task are those of the test's directory, so that the
    script finds them; the task's own executor gets the decrypted file,
    which the teardown executor removes.

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
    assert executors[2]["command"] == ["crypt4gh-decrypt", "--clear", str(volume)]
    assert decrypt.main(executors[2]["command"][1:]) == 0
    assert list(volume.iterdir()) == []
