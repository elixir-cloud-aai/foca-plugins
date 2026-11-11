#!/usr/bin/env python3
"""Decryption executor of `tes.data.crypt4gh`: decrypt Crypt4GH files.

Runs in the decryption image (`image/Dockerfile`), as the first executor of
a task, never in the gateway: the image copies this file alone, so it uses
the standard library and the `crypt4gh` package only (which the plugin
doesn't depend on). Gateway-neutral: it gets paths only.

Usage::

    crypt4gh-decrypt --key KEY [--key KEY ...]
        --file SOURCE TARGET [--file SOURCE TARGET ...]

Each `SOURCE` (a Crypt4GH file) is decrypted with the secret keys (Crypt4GH
or OpenSSH ed25519 keys without passphrase; a file decrypts with any of
them) to `TARGET`. Sources are only read, so they may be on read-only file
systems; targets are written to a temporary file next to them first, and
are readable by all (0644), as the task's next executors may run as other
users. If a key can't be read or a file can't be decrypted, the targets
written so far are removed and the script exits with status 1, so that the
task's own executors don't run.
"""

import argparse
from collections.abc import Sequence
import logging
from pathlib import Path
import sys
import tempfile

from crypt4gh.keys import get_private_key  # type: ignore[import-untyped]
from crypt4gh.lib import decrypt  # type: ignore[import-untyped]

logger = logging.getLogger("crypt4gh-decrypt")

# Crypt4GH's code of the bulk encryption method of its header packets
CHACHA20_POLY1305 = 0
# Permissions of decrypted files, and of the directories they are written to
FILE_MODE = 0o644
DIRECTORY_MODE = 0o755


class DecryptionError(Exception):
    """A key can't be read or a file can't be decrypted."""


def load_keys(paths: Sequence[Path]) -> list[bytes]:
    """Read secret keys.

    Args:
        paths: Paths of the keys.

    Returns:
        The keys.

    Raises:
        DecryptionError: A key can't be read, is no secret key, or has a
            passphrase.
    """
    keys = []
    for path in paths:
        try:
            keys.append(get_private_key(str(path), callback=lambda: ""))
        # `crypt4gh` exits on keys with a passphrase
        except (Exception, SystemExit) as error:
            raise DecryptionError(
                f"Can't read secret key {path} (no Crypt4GH or OpenSSH secret key"
                " without passphrase)"
            ) from error
    return keys


def decrypt_file(source: Path, target: Path, keys: Sequence[bytes]) -> None:
    """Decrypt a Crypt4GH file.

    Args:
        source: Crypt4GH file.
        target: Path of the decrypted file; its directory must exist.
        keys: Secret keys.

    Raises:
        DecryptionError: The file can't be read or decrypted with the keys.
    """
    key_tuples = [(CHACHA20_POLY1305, key, None) for key in keys]
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", delete=False
    ) as part:
        try:
            with source.open("rb") as infile:
                decrypt(keys=key_tuples, infile=infile, outfile=part)
        except Exception as error:
            part.close()
            Path(part.name).unlink()
            raise DecryptionError(f"Can't decrypt {source}: {error}") from error
    Path(part.name).chmod(FILE_MODE)
    Path(part.name).replace(target)


def open_directories(targets: Sequence[Path]) -> None:
    """Create the directories of the targets, readable by all.

    Args:
        targets: Paths of the decrypted files.
    """
    for directory in {target.parent for target in targets}:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(DIRECTORY_MODE)
        except OSError as error:  # e.g., not its owner: keep its permissions
            logger.warning("Can't change permissions of %s: %s", directory, error)


def remove(paths: Sequence[Path]) -> None:
    """Remove files that exist.

    Args:
        paths: Paths of the files.
    """
    for path in paths:
        path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the arguments.

    Args:
        argv: Arguments; defaults to those of the command line.

    Returns:
        Arguments `keys` (paths) and `files` (pairs of source and target
        paths).
    """
    parser = argparse.ArgumentParser(
        prog="crypt4gh-decrypt", description="Decrypt Crypt4GH files."
    )
    parser.add_argument(
        "--key",
        dest="keys",
        action="append",
        required=True,
        type=Path,
        metavar="KEY",
        help="secret key (repeatable)",
    )
    parser.add_argument(
        "--file",
        dest="files",
        action="append",
        required=True,
        nargs=2,
        type=Path,
        metavar=("SOURCE", "TARGET"),
        help="Crypt4GH file and path of the decrypted file (repeatable)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Decrypt the files of the arguments.

    Args:
        argv: Arguments; defaults to those of the command line.

    Returns:
        Exit status: 0 if all files were decrypted, 1 otherwise.
    """
    # Warnings of `crypt4gh` only: its debug messages include key material
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    logger.setLevel(logging.INFO)
    args = parse_args(argv)
    targets = [target for _, target in args.files]
    written: list[Path] = []
    try:
        if len(set(targets)) != len(targets):
            raise DecryptionError("Targets must differ")
        keys = load_keys(args.keys)
        open_directories(targets)
        for source, target in args.files:
            decrypt_file(source, target, keys)
            written.append(target)
            logger.info("Decrypted %s to %s", source, target)
    except DecryptionError as error:
        remove(written)
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
