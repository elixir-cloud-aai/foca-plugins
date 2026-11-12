"""TES-specific part of `tes.data.crypt4gh`: the rewrite of the task.

The task gets a volume for the decrypted files and, as its first executor,
the decryption executor, which writes the decrypted files to the volume;
the arguments of the task's own executors that name a Crypt4GH input are
changed to its decrypted file. As its last executor, the task may get the
teardown executor, which removes the decrypted files again. Which inputs
are Crypt4GH files and keys is decided by the gateway-neutral
:py:mod:`.inputs`.

Fields and items of the wrong type (e.g., `inputs` that is no list, or
items that are no objects) are skipped and kept as they are, and a task
whose `executors` is absent, null, empty or no list, or whose `volumes` is
no list, is not changed: the gateway app validates the task after the plugin
and rejects it. Relative paths of inputs, outputs and volumes are rejected
by the plugin, as it can't tell where they lead.
"""

from collections.abc import Sequence
import fnmatch
from typing import Any, TypeGuard

from foca_plugins.tes.data.crypt4gh.inputs import (
    Crypt4ghInputs,
    InputError,
    normalize,
    overlaps,
)


def malformed(task: dict[str, Any], *fields: str, nullable: Sequence[str] = ()) -> bool:
    """Tell whether a task has list fields of another type.

    Args:
        task: TES task.
        fields: Names of the list fields.
        nullable: Names of the fields that may be null, which then count as
            absent.

    Returns:
        Whether one of the fields is present and no list (or null, if it
        isn't nullable).
    """
    return any(
        field in task
        and not isinstance(task[field], list)
        and not (task[field] is None and field in nullable)
        for field in fields
    )


def items(task: dict[str, Any], field: str) -> list[Any]:
    """Get the items of a list field of a task.

    Args:
        task: TES task.
        field: Name of the field, e.g., `inputs`.

    Returns:
        The items, or no items if the field is absent or no list.
    """
    value = task.get(field)
    return value if isinstance(value, list) else []


def objects(task: dict[str, Any], field: str) -> list[tuple[int, dict[str, Any]]]:
    """Get the objects of a list field of a task, with their positions.

    Args:
        task: TES task.
        field: Name of the field, e.g., `inputs`.

    Returns:
        Position and value of each item that is an object.
    """
    return [
        (i, item) for i, item in enumerate(items(task, field)) if isinstance(item, dict)
    ]


def input_paths(task: dict[str, Any]) -> list[str | None]:
    """Get the paths of the input files of a task.

    Args:
        task: TES task.

    Returns:
        The path of each input, in order; `None` for directories (`type:
        DIRECTORY`), inputs without a path and items that are no objects.
    """
    paths: list[str | None] = []
    for item in items(task, "inputs"):
        path = item.get("path") if isinstance(item, dict) else None
        # A null type is that of a file, as for the backends
        is_file = isinstance(item, dict) and (item.get("type") or "FILE") == "FILE"
        paths.append(path if is_file and isinstance(path, str) else None)
    return paths


def absolute(value: Any) -> TypeGuard[str]:
    """Tell whether a value is an absolute path.

    Args:
        value: Path, argument or other value of a task.

    Returns:
        Whether `value` is a string that starts with `/`.
    """
    return isinstance(value, str) and value.startswith("/")


WILDCARDS = frozenset("*?[")
"""Characters of the POSIX wildcards that TES allows in output paths."""


def fixed_directory(path: str) -> str:
    """Get the directory that an output path with wildcards is restricted to.

    Args:
        path: Absolute output path.

    Returns:
        The path itself if it has no wildcards, else the directory of its
        part before the first wildcard (e.g., `/` for `/v*/crypt4gh/*`).
    """
    index = next((i for i, char in enumerate(path) if char in WILDCARDS), None)
    if index is None:
        return path
    return path[:index].rsplit("/", 1)[0] or "/"


def check_paths(task: dict[str, Any], volume: str, own: int | None = None) -> None:
    """Check that the task leaves the volume of the decrypted files alone.

    Args:
        task: TES task.
        volume: Path of the volume of the decrypted files.
        own: Position of the volume among the task's `volumes` if the plugin
            added it (cf. :py:func:`rewritten`), which is then not checked.

    Raises:
        InputError: An input, output or volume of the task has a relative
            path, or is the volume, inside it or one of its parent
            directories (cf. :py:func:`.inputs.overlaps`); for outputs with
            wildcards, the directory before the first wildcard counts.
    """
    paths = [
        (f"{field}[{index}]", item.get("path"))
        for field in ("inputs", "outputs")
        for index, item in objects(task, field)
    ]
    paths += [
        (f"volumes[{index}]", path)
        for index, path in enumerate(items(task, "volumes"))
        if index != own
    ]
    for name, path in paths:
        if not isinstance(path, str):
            continue
        if not absolute(path):
            raise InputError(f"{name}: path must be absolute")
        checked = fixed_directory(path) if name.startswith("outputs") else path
        if overlaps(checked, volume):
            raise InputError(f"{name}: path is reserved for decrypted files")


def alias(value: Any, targets: dict[str, str]) -> Any:
    """Get the decrypted file for an argument that names a Crypt4GH input.

    Args:
        value: Argument, stdin or output path.
        targets: Path of the decrypted file for each Crypt4GH input.

    Returns:
        The path of the decrypted file if `value` is the absolute path of a
        Crypt4GH input, in any spelling (e.g., `//in/a.c4gh`); else `value`.
    """
    if absolute(value):
        return targets.get(normalize(value), value)
    return value


def check_outputs(task: dict[str, Any], found: Crypt4ghInputs) -> None:
    """Check that no output of the task is a Crypt4GH input.

    The task's executors work on the decrypted files, so an output at the
    path of a Crypt4GH input would return the encrypted input, not a result.

    Args:
        task: TES task.
        found: Crypt4GH files and keys among the task's inputs.

    Raises:
        InputError: An output has the path of a Crypt4GH input, also in
            another spelling (e.g., `/in/./a.c4gh` for `/in/a.c4gh`), or
            has wildcards that match one (e.g., `/in/*.c4gh`).
    """
    encrypted = {file.path for file in found.files}
    for index, item in objects(task, "outputs"):
        path = item.get("path")
        # Compared as is, too, for names with `[` (e.g., `/in/a[1].c4gh`)
        if absolute(path) and (
            normalize(path) in encrypted
            or any(fnmatch.fnmatchcase(file, normalize(path)) for file in encrypted)
        ):
            raise InputError(
                f"outputs[{index}]: path is that of a Crypt4GH input, which is"
                " not changed"
            )


def target(volume: str, name: str) -> str:
    """Get the path of a decrypted file.

    Args:
        volume: Path of the volume of the decrypted files.
        name: Name of the decrypted file.

    Returns:
        Path of the decrypted file in the volume.
    """
    return f"{volume}/{name}"


def decryption_executor(
    found: Crypt4ghInputs, *, image: str, command: Sequence[str], volume: str
) -> dict[str, Any]:
    """Build the decryption executor.

    Args:
        found: Crypt4GH files and keys among the task's inputs.
        image: Image of the decryption executor.
        command: Command of the decryption script in the image.
        volume: Path of the volume of the decrypted files.

    Returns:
        TES executor that decrypts each Crypt4GH input to the volume (cf.
        :py:mod:`.decrypt` for the arguments).
    """
    arguments = list(command)
    for key in found.keys:
        arguments += ["--key", key]
    for file in found.files:
        arguments += ["--file", file.path, target(volume, file.name)]
    return {"image": image, "command": arguments}


def teardown_executor(
    found: Crypt4ghInputs, *, image: str, command: Sequence[str], volume: str
) -> dict[str, Any]:
    """Build the teardown executor.

    Args:
        found: Crypt4GH files and keys among the task's inputs.
        image: Image of the decryption executor.
        command: Command of the decryption script in the image.
        volume: Path of the volume of the decrypted files.

    Returns:
        TES executor that removes each decrypted file from the volume (cf.
        :py:mod:`.decrypt` for the arguments).
    """
    arguments = list(command)
    for file in found.files:
        arguments += ["--remove", target(volume, file.name)]
    return {"image": image, "command": arguments}


def rewritten(
    task: dict[str, Any],
    found: Crypt4ghInputs,
    *,
    image: str,
    command: Sequence[str],
    volume: str,
) -> int | None:
    """Tell whether the plugin has added the decryption to a task already.

    Args:
        task: TES task.
        found: Crypt4GH files and keys among the task's inputs.
        image: Image of the decryption executor.
        command: Command of the decryption script in the image.
        volume: Path of the volume of the decrypted files.

    Returns:
        The position of the volume among the task's `volumes` if the volume
        is there once and the first executor is the decryption executor
        that the plugin would add; else `None`.
    """
    executors = items(task, "executors")
    volumes = items(task, "volumes")
    expected = decryption_executor(found, image=image, command=command, volume=volume)
    if executors[:1] != [expected] or volumes.count(volume) != 1:
        return None
    return volumes.index(volume)


def rewrite(
    task: dict[str, Any],
    found: Crypt4ghInputs,
    *,
    image: str,
    command: Sequence[str],
    volume: str,
    again: bool = False,
    teardown: bool = False,
    ignore_errors: bool = False,
) -> None:
    """Add the decryption to a task, in place.

    Args:
        task: TES task, with Crypt4GH inputs.
        found: Crypt4GH files and keys among the task's inputs.
        image: Image of the decryption executor.
        command: Command of the decryption script in the image.
        volume: Path of the volume of the decrypted files.
        again: Whether the task has the decryption already (cf.
            :py:func:`rewritten`); then only the executors after the
            decryption executor are changed, and the volume isn't added
            again.
        teardown: Whether to add the teardown executor after the task's
            own executors; copies of it among them are dropped, so that it
            runs once, at the end.
        ignore_errors: Whether the task's own executors get `ignore_error`,
            so that the teardown executor runs after they fail (if the
            backend supports it); the decryption and teardown executors
            don't get it.
    """
    targets = {file.path: target(volume, file.name) for file in found.files}
    decryption = decryption_executor(found, image=image, command=command, volume=volume)
    removal = teardown_executor(found, image=image, command=command, volume=volume)
    own = items(task, "executors")[1 if again else 0 :]
    if teardown:
        own = [executor for executor in own if executor != removal]
    for executor in own:
        if not isinstance(executor, dict):
            continue
        args = executor.get("command")
        if isinstance(args, list):
            executor["command"] = [alias(arg, targets) for arg in args]
        if "stdin" in executor:
            executor["stdin"] = alias(executor["stdin"], targets)
        if ignore_errors:
            executor["ignore_error"] = True
    task["executors"] = [decryption, *own, *([removal] if teardown else [])]
    if not again:
        task["volumes"] = [*items(task, "volumes"), volume]
