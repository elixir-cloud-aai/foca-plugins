"""Crypt4GH inputs of a request: the files to decrypt and the secret keys.

Gateway-neutral: this module works on the paths of input files only, not on
TES tasks, so that a plugin for another API (e.g., WES) can reuse it. Files
are told apart by the suffixes of their names, as the gateway can't read
their contents: Crypt4GH files (by default `.c4gh`) and secret keys (by
default `.sec`). The decryption executor (:py:mod:`.decrypt`) checks both
when the task runs.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath


class InputError(ValueError):
    """The inputs of a request can't be decrypted as they are.

    The message names the positions of the inputs, not their paths, so that
    it can be passed to clients and logs as it is.
    """


@dataclass(frozen=True)
class EncryptedFile:
    """An input file to decrypt.

    Attributes:
        index: Position of the input in the request.
        path: Path of the input file.
        name: Name of the decrypted file: the file name without its
            Crypt4GH suffix.
    """

    index: int
    path: str
    name: str


@dataclass(frozen=True)
class Crypt4ghInputs:
    """The Crypt4GH files and secret keys among the inputs of a request.

    Attributes:
        files: Input files to decrypt, in the order of the inputs.
        keys: Paths of the secret keys, in the order of the inputs.
    """

    files: tuple[EncryptedFile, ...] = ()
    keys: tuple[str, ...] = ()


def decrypted_name(path: str, suffixes: Iterable[str]) -> str | None:
    """Get the name of the decrypted file of an input file.

    Args:
        path: Path of the input file.
        suffixes: Suffixes of Crypt4GH files; the longest that matches is
            removed.

    Returns:
        The file name without the suffix (possibly empty), or `None` if the
        name has none of the suffixes.
    """
    name = PurePosixPath(path).name
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def normalize(path: str) -> str:
    """Normalize an absolute path, as the container's file system reads it.

    Args:
        path: Absolute path.

    Returns:
        The path without `.`, `..` (resolved lexically), repeated and
        trailing slashes, e.g., `/in/a` for `//in/./x/../a/`.
    """
    return "/" + posixpath.normpath(path).lstrip("/")


def within(path: str, directory: str) -> bool:
    """Tell whether a path is a directory or inside it.

    Args:
        path: Absolute path.
        directory: Absolute, normalized path of the directory.

    Returns:
        Whether `path` is `directory` or below it, also after normalizing it
        (e.g., `/vol/x/../crypt4gh` or `//vol/crypt4gh` for `/vol/crypt4gh`),
        or as it is (as `..` after a symbolic link leads elsewhere).
    """
    return any(
        PurePosixPath(candidate).is_relative_to(PurePosixPath(directory))
        for candidate in (path, normalize(path))
    )


def overlaps(path: str, directory: str) -> bool:
    """Tell whether a path is a directory, inside it, or one of its parents.

    Args:
        path: Absolute path.
        directory: Absolute, normalized path of the directory.

    Returns:
        Whether `path` is `directory`, below it (cf. :py:func:`within`) or
        above it (normalized), so that mounting either changes the other.
    """
    return within(path, directory) or within(directory, normalize(path))


def classify(
    paths: Sequence[str | None],
    *,
    encrypted_suffixes: Iterable[str],
    key_suffixes: Iterable[str],
    field: str = "inputs",
) -> Crypt4ghInputs:
    """Find the Crypt4GH files and secret keys among the input files.

    Args:
        paths: Paths of the inputs, in order; `None` for inputs that are no
            files (e.g., directories), which are skipped.
        encrypted_suffixes: Suffixes of the names of Crypt4GH files.
        key_suffixes: Suffixes of the names of secret keys.
        field: Name of the inputs in messages, e.g., `inputs` for the
            `inputs` of a TES task.

    Returns:
        The Crypt4GH files and the secret keys; both empty if no input is a
        Crypt4GH file or a key.

    Raises:
        InputError: A Crypt4GH file or key has no absolute, normalized
            path, a Crypt4GH file has no name without its suffix (or `.` or
            `..`), two Crypt4GH files decrypt to the same name, or there are
            Crypt4GH files but no keys.
    """
    encrypted = tuple(encrypted_suffixes)
    keys_suffixes = tuple(key_suffixes)
    files: list[EncryptedFile] = []
    keys: list[str] = []
    names: dict[str, int] = {}
    for index, path in enumerate(paths):
        if path is None:
            continue
        name = decrypted_name(path, encrypted)
        is_key = name is None and decrypted_name(path, keys_suffixes) is not None
        if name is None and not is_key:
            continue
        # Normalized, so that the paths of executors and outputs that name
        # the input are recognized by plain comparison
        if normalize(path) != path:
            raise InputError(f"{field}[{index}]: path must be absolute and normalized")
        if is_key:
            keys.append(path)
            continue
        if name is None or name in ("", ".", ".."):
            raise InputError(
                f"{field}[{index}]: Crypt4GH file needs a name besides its suffix"
            )
        if name in names:
            raise InputError(
                f"{field}[{names[name]}] and {field}[{index}]: Crypt4GH files"
                " decrypt to the same file name"
            )
        names[name] = index
        files.append(EncryptedFile(index=index, path=path, name=name))
    if files and not keys:
        raise InputError(
            f"{field}: Crypt4GH files need a secret key among the inputs (a file"
            f" name ending in {' or '.join(keys_suffixes)})"
        )
    return Crypt4ghInputs(files=tuple(files), keys=tuple(keys))
