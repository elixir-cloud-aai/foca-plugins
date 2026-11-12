#!/usr/bin/env python3
"""Decryption executor of `tes.data.crypt4gh`: decrypt Crypt4GH files.

Runs in the decryption image (`image/Dockerfile`), as the first executor of
a task and as its last (the teardown executor), never in the gateway: the
image copies this file alone, so it uses the standard library and the
`crypt4gh` package only (which the plugin doesn't depend on).
Gateway-neutral: it gets paths only.

Usage::

    crypt4gh-decrypt --key KEY [--key KEY ...]
        --file SOURCE TARGET [--file SOURCE TARGET ...]
    crypt4gh-decrypt --clear DIRECTORY

Each `SOURCE` (a Crypt4GH file) is decrypted with the secret keys (Crypt4GH
or OpenSSH ed25519 keys without passphrase; a file decrypts with any of
them) to `TARGET`. Sources are only read, so they may be on read-only file
systems; targets are written to a temporary file next to them first, and
are readable by all (0644), as the task's next executors may run as other
users. If a key can't be read or a file can't be decrypted, the targets
written so far are removed and the script exits with status 1, so that the
task's own executors don't run.

With `--clear`, the script removes everything in `DIRECTORY` (the volume of
the decrypted files, which belongs to the plugin), but keeps the directory
itself, which may be a mount point: the decrypted files, temporary files
of a stopped decryption, and files that the task's executors derived from
the decrypted files there. Symbolic links are removed, not followed. A
missing directory is fine, so that it may run again; if an entry can't be
removed, it goes on with the others and exits with status 1.
"""

import argparse
from collections.abc import Sequence
import logging
import os
from pathlib import Path
import shutil
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
        # `crypt4gh` 1.8 exits on keys that it can't unlock, 1.7 raises an
        # error without message; other errors say what is wrong (e.g., a
        # missing file, no key)
        except (Exception, SystemExit) as error:
            reason = "" if isinstance(error, SystemExit) else str(error)
            raise DecryptionError(
                f"Can't read secret key {path}:"
                f" {reason or 'it has a passphrase or is invalid'}"
            ) from error
    return keys


def decrypt_file(source: Path, target: Path, keys: Sequence[bytes]) -> None:
    """Decrypt a Crypt4GH file.

    The decrypted data goes to a temporary file next to the target first,
    which is removed on any error or exception, unless it became the
    target.

    Args:
        source: Crypt4GH file.
        target: Path of the decrypted file; its directory must exist.
        keys: Secret keys.

    Raises:
        DecryptionError: The file can't be read or decrypted with the keys,
            or the target can't be written.
    """
    key_tuples = [(CHACHA20_POLY1305, key, None) for key in keys]
    part: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as outfile:
            part = Path(outfile.name)
            with source.open("rb") as infile:
                decrypt(keys=key_tuples, infile=infile, outfile=outfile)
        part.chmod(FILE_MODE)
        part.replace(target)
        part = None
    except Exception as error:
        raise DecryptionError(f"Can't decrypt {source}: {error}") from error
    finally:
        if part is not None:
            try:
                part.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("Can't remove temporary file %s: %s", part, error)


def open_directories(targets: Sequence[Path]) -> None:
    """Create the directories of the targets, readable by all.

    Args:
        targets: Paths of the decrypted files.

    Raises:
        DecryptionError: A directory can't be created.
    """
    for directory in {target.parent for target in targets}:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DecryptionError(f"Can't create {directory}: {error}") from error
        try:
            directory.chmod(DIRECTORY_MODE)
        except OSError as error:  # e.g., not its owner: keep its permissions
            logger.warning("Can't change permissions of %s: %s", directory, error)


def remove(paths: Sequence[Path]) -> None:
    """Remove files that exist, as far as possible.

    Args:
        paths: Paths of the files.
    """
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:  # go on with the others
            logger.warning("Can't remove %s: %s", path, error)


def clear(directory: Path) -> bool:
    """Remove everything in a directory, as far as possible, but keep it.

    Args:
        directory: Path of the directory.

    Returns:
        Whether the directory is empty or missing.
    """
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return True
    except OSError as error:
        logger.warning("Can't list %s: %s", directory, error)
        return False
    done = True
    for entry in entries:
        try:
            # Links are removed, never followed
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:  # go on with the others
            logger.warning("Can't remove %s: %s", entry, error)
            done = False
            continue
        logger.info("Removed %s", entry)
    return done


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the arguments.

    Args:
        argv: Arguments; defaults to those of the command line.

    Returns:
        Arguments `keys` (paths) and `files` (pairs of source and target
        paths), or `clear` (path of the directory to empty); those not
        given are `None`.
    """
    parser = argparse.ArgumentParser(
        prog="crypt4gh-decrypt",
        description="Decrypt Crypt4GH files, or remove the decrypted files.",
    )
    parser.add_argument(
        "--key",
        dest="keys",
        action="append",
        type=Path,
        metavar="KEY",
        help="secret key (repeatable; required with --file)",
    )
    parser.add_argument(
        "--file",
        dest="files",
        action="append",
        nargs=2,
        type=Path,
        metavar=("SOURCE", "TARGET"),
        help="Crypt4GH file and path of the decrypted file (repeatable)",
    )
    parser.add_argument(
        "--clear",
        type=Path,
        metavar="DIRECTORY",
        help=(
            "absolute path of the directory of the decrypted files, whose"
            " contents to remove (not with --key or --file)"
        ),
    )
    args = parser.parse_args(argv)
    if args.clear is not None:
        if args.keys or args.files:
            parser.error("--clear can't be combined with --key or --file")
        # Resolved, so that `//`, `/x/..` or a link to `/` count as `/`
        if not args.clear.is_absolute() or os.path.realpath(args.clear) == "/":
            parser.error("--clear needs an absolute path other than /")
        return args
    missing = [
        option
        for option, value in (("--key", args.keys), ("--file", args.files))
        if not value
    ]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Decrypt the files of the arguments, or remove the decrypted files.

    Args:
        argv: Arguments; defaults to those of the command line.

    Returns:
        Exit status: 0 if all files were decrypted (or the directory was
        emptied), 1 otherwise.
    """
    # Warnings of `crypt4gh` only: its debug messages include key material
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    logger.setLevel(logging.INFO)
    args = parse_args(argv)
    if args.clear is not None:
        return 0 if clear(args.clear) else 1
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
