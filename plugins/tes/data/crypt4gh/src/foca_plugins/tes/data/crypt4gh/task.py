"""TES-specific part of `tes.data.crypt4gh`: the rewrite of the task.

The task gets a volume for the decrypted files and, as its first executor,
the decryption executor, which writes the decrypted files to the volume;
the arguments of the task's own executors that name a Crypt4GH input are
changed to its decrypted file. Which inputs are Crypt4GH files and keys is
decided by the gateway-neutral :py:mod:`.inputs`.

Fields of the wrong type (e.g., `inputs` that is no list, or items that are
no objects) are taken as absent: the gateway app validates the task after
the plugin and rejects it.
"""

from collections.abc import Sequence
from typing import Any

from foca_plugins.tes.data.crypt4gh.inputs import Crypt4ghInputs, InputError, within


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
        is_file = isinstance(item, dict) and item.get("type", "FILE") == "FILE"
        paths.append(path if is_file and isinstance(path, str) else None)
    return paths


def check_paths(task: dict[str, Any], volume: str) -> None:
    """Check that the task leaves the volume of the decrypted files alone.

    Args:
        task: TES task.
        volume: Path of the volume of the decrypted files.

    Raises:
        InputError: An input, output or volume of the task is the volume or
            inside it.
    """
    for field in ("inputs", "outputs"):
        for index, item in objects(task, field):
            path = item.get("path")
            if isinstance(path, str) and within(path, volume):
                raise InputError(
                    f"{field}[{index}]: path is reserved for decrypted files"
                )
    for index, path in enumerate(items(task, "volumes")):
        if isinstance(path, str) and within(path, volume):
            raise InputError(f"volumes[{index}]: path is reserved for decrypted files")


def check_outputs(task: dict[str, Any], found: Crypt4ghInputs) -> None:
    """Check that no output of the task is a Crypt4GH input.

    The task's executors work on the decrypted files, so an output at the
    path of a Crypt4GH input would return the encrypted input, not a result.

    Args:
        task: TES task.
        found: Crypt4GH files and keys among the task's inputs.

    Raises:
        InputError: An output has the path of a Crypt4GH input.
    """
    encrypted = {file.path for file in found.files}
    for index, item in objects(task, "outputs"):
        if item.get("path") in encrypted:
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


def rewrite(
    task: dict[str, Any],
    found: Crypt4ghInputs,
    *,
    image: str,
    command: Sequence[str],
    volume: str,
) -> None:
    """Add the decryption to a task, in place.

    Args:
        task: TES task, with Crypt4GH inputs.
        found: Crypt4GH files and keys among the task's inputs.
        image: Image of the decryption executor.
        command: Command of the decryption script in the image.
        volume: Path of the volume of the decrypted files.
    """
    targets = {file.path: target(volume, file.name) for file in found.files}
    for _, executor in objects(task, "executors"):
        args = executor.get("command")
        if isinstance(args, list):
            executor["command"] = [
                targets.get(arg, arg) if isinstance(arg, str) else arg for arg in args
            ]
        stdin = executor.get("stdin")
        if isinstance(stdin, str) and stdin in targets:
            executor["stdin"] = targets[stdin]
    task["executors"] = [
        decryption_executor(found, image=image, command=command, volume=volume),
        *items(task, "executors"),
    ]
    task["volumes"] = [*items(task, "volumes"), volume]
