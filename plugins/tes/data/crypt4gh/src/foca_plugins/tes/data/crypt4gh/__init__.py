"""Plugin `tes.data.crypt4gh`: decrypt Crypt4GH inputs in an extra executor.

The successor of the Crypt4GH middleware of proTES
(`protes-middleware-crypt4gh`). TES implementations can't decrypt
Crypt4GH files; this plugin adds, in the `request` phase, a decryption
executor before the task's own executors, which decrypts the task's
Crypt4GH inputs with the secret keys among its inputs to a volume of their
own, and changes the arguments of the task's executors that name a
Crypt4GH input to its decrypted file.

Parts:

- :py:mod:`.inputs`: which inputs are Crypt4GH files and keys
  (gateway-neutral);
- :py:mod:`.task`: the rewrite of the TES task;
- :py:mod:`.decrypt`: the decryption script, which runs in the decryption
  image (`image/Dockerfile`), not in the gateway.

The plugin API is documented in FOCA's `docs/plugins.md`
(https://foca.readthedocs.io/en/latest/plugins/).
"""

from pathlib import PurePosixPath
from typing import Annotated, Any, Self

from foca.plugins import Context, InboundRequest, NotApplicable, Plugin, Rejected
from foca.plugins.base import EmptyModel
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from foca_plugins.tes.data.crypt4gh.inputs import InputError, classify
from foca_plugins.tes.data.crypt4gh.task import (
    check_outputs,
    check_paths,
    input_paths,
    malformed,
    rewrite,
    rewritten,
)

Suffix = Annotated[str, Field(pattern=r"^\.[^/\s]{1,32}$")]
"""Suffix of file names: a dot and 1 to 32 characters, no slash or space."""


class Crypt4ghConfig(BaseModel):
    """Admin settings (`plugins.available."tes.data.crypt4gh".config`).

    Attributes:
        image: Image of the decryption executor, built from `image/Dockerfile`
            of the plugin; best by digest.
        command: Command of the decryption script in the image.
        volume: Path of the volume of the decrypted files; tasks may not use
            it or paths in it.
        encrypted_suffixes: Suffixes of the names of Crypt4GH inputs.
        key_suffixes: Suffixes of the names of secret keys among the inputs.
    """

    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1, max_length=512, pattern=r"^\S+$")
    command: tuple[Annotated[str, Field(min_length=1, max_length=4096)], ...] = Field(
        default=("crypt4gh-decrypt",), min_length=1, max_length=16
    )
    volume: str = Field(default="/vol/crypt4gh", max_length=1024)
    encrypted_suffixes: tuple[Suffix, ...] = Field(
        default=(".c4gh",), min_length=1, max_length=16
    )
    key_suffixes: tuple[Suffix, ...] = Field(
        default=(".sec",), min_length=1, max_length=16
    )

    @field_validator("volume")
    @classmethod
    def _check_volume(cls, value: str) -> str:
        """Check that the volume is an absolute, normalized path below `/`.

        Args:
            value: Path of the volume.

        Returns:
            The path.

        Raises:
            ValueError: The path is not absolute, not normalized (e.g., with
                `.`, `..`, `//` or a trailing slash), or `/`.
        """
        path = PurePosixPath(value)
        if (
            not path.is_absolute()
            or str(path) != value
            or ".." in path.parts
            or value == "/"
        ):
            raise ValueError("must be an absolute, normalized path below /")
        return value

    @model_validator(mode="after")
    def _check_suffixes(self) -> Self:
        """Check that no suffix is both that of Crypt4GH files and of keys.

        Returns:
            The settings.

        Raises:
            ValueError: A suffix of Crypt4GH files ends with one of keys, or
                the other way round (e.g., `.key.c4gh` and `.c4gh`), so that
                a file name would have both.
        """
        if any(
            a.endswith(b) or b.endswith(a)
            for a in self.encrypted_suffixes
            for b in self.key_suffixes
        ):
            raise ValueError(
                "encrypted_suffixes and key_suffixes must differ and not end"
                " with one another"
            )
        return self


class Crypt4gh(Plugin[Crypt4ghConfig, EmptyModel]):
    """Decrypt Crypt4GH inputs in an extra executor.

    Attributes:
        settings: Validated admin settings.
    """

    Config = Crypt4ghConfig
    apis = frozenset({"tes"})
    capabilities = frozenset({"crypt4gh-decryption"})
    maturity = "experimental"
    description = (
        "Adds an executor before the task's own ones that decrypts the task's"
        " Crypt4GH inputs with the secret keys among its inputs to a volume of"
        " their own, and points the arguments of the task's executors that name"
        " a Crypt4GH input to its decrypted file. Leaves tasks without Crypt4GH"
        " inputs as they are."
    )
    data_transfers = ()
    # Nothing in the plugin store (listed by FOCA 0.19; declared here, as
    # FOCA 0.18 has no such attribute)
    data_stored = ()

    settings: Crypt4ghConfig

    def setup(self, config: Crypt4ghConfig) -> None:
        """Keep the settings; idempotent, as `setup()` may run more than once.

        Args:
            config: Validated admin settings.
        """
        self.settings = config

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Add the decryption executor and volume for Crypt4GH inputs.

        Args:
            request: Inbound request (a copy, which the engine keeps).
            ctx: Context of the call.

        Raises:
            NotApplicable: The payload is no task (no JSON object).
            Rejected: With `422`, if the Crypt4GH inputs can't be decrypted
                as they are (cf. :py:func:`.inputs.classify`), or if the task
                uses the volume of the decrypted files or has an output at
                the path of a Crypt4GH input.
        """
        task: Any = request.payload
        if not isinstance(task, dict):
            raise NotApplicable("payload is no task")
        settings = self.settings
        try:
            found = classify(
                input_paths(task),
                encrypted_suffixes=settings.encrypted_suffixes,
                key_suffixes=settings.key_suffixes,
            )
            ctx.record("encrypted_inputs", len(found.files))
            ctx.record("key_inputs", len(found.keys))
            # Fields of the wrong type are left for the app to reject
            if not found.files or malformed(
                task, "executors", "volumes", nullable=("volumes",)
            ):
                return
            # A task changed already (e.g., sent through the gateway twice)
            if rewritten(
                task,
                image=settings.image,
                command=settings.command,
                volume=settings.volume,
            ):
                return
            check_paths(task, settings.volume)
            check_outputs(task, found)
        except InputError as error:
            raise Rejected(str(error), 422) from error
        rewrite(
            task,
            found,
            image=settings.image,
            command=settings.command,
            volume=settings.volume,
        )
