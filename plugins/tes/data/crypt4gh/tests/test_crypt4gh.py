"""Tests of the plugin `tes.data.crypt4gh`.

Unit tests call the hook with a context of their own; the other tests use
FOCA's plugin test kit (`foca.plugins.testing`): the contract checks, a
harness that runs the plugin through the real engine, and a pipeline that
loads the plugin through its installed entry point, as gateway apps do.
"""

from collections.abc import Iterator
import copy
from importlib.metadata import entry_points
import time
from typing import Any

from foca.plugins import Context, NotApplicable, Principal, Rejected
from foca.plugins.base import EmptyModel, check_plugin_class
from foca.plugins.config import PluginsConfig
from foca.plugins.engine import PluginHost
from foca.plugins.testing import PluginHarness, check_plugin_contract, make_inbound
from pydantic import ValidationError
import pytest

from foca_plugins.tes.data.crypt4gh import Crypt4gh, Crypt4ghConfig

PLUGIN_ID = "tes.data.crypt4gh"
IMAGE = "docker.io/example/crypt4gh-decrypt@sha256:" + "0" * 64
CONFIG = {"image": IMAGE}
VOLUME = "/vol/crypt4gh"


def make_task(**fields: Any) -> dict[str, Any]:
    """Build a task with a Crypt4GH input and a key, new for each use.

    Args:
        fields: Fields that replace those of the task.

    Returns:
        Task whose executor reads the Crypt4GH input.
    """
    task: dict[str, Any] = {
        "inputs": [
            {"url": "s3://bucket/reads.fq.c4gh", "path": "/in/reads.fq.c4gh"},
            {"url": "s3://bucket/alice.sec", "path": "/keys/alice.sec"},
            {"url": "s3://bucket/ref.fa", "path": "/in/ref.fa"},
        ],
        "outputs": [{"url": "s3://bucket/out", "path": "/out/result"}],
        "executors": [
            {
                "image": "aligner",
                "command": ["align", "/in/ref.fa", "/in/reads.fq.c4gh", "/out"],
            }
        ],
    }
    task.update(fields)
    return task


def decryption_executor(*files: tuple[str, str]) -> dict[str, Any]:
    """Build the expected decryption executor, with the key of `make_task()`.

    Args:
        files: Pairs of Crypt4GH input and decrypted file.

    Returns:
        Executor.
    """
    command = ["crypt4gh-decrypt", "--key", "/keys/alice.sec"]
    for source, target in files:
        command += ["--file", source, target]
    return {"image": IMAGE, "command": command}


def teardown_executor() -> dict[str, Any]:
    """Build the expected teardown executor, with the default volume.

    Returns:
        Executor.
    """
    return {"image": IMAGE, "command": ["crypt4gh-decrypt", "--clear", VOLUME]}


def make_context() -> Context:
    """Build the context of a hook call.

    Returns:
        Context.
    """
    return Context(
        api="tes",
        plugin_id=PLUGIN_ID,
        principal=Principal(subject="alice"),
        params=EmptyModel(),
        original=make_task(),
        deadline=time.monotonic() + 10,
        state={},
        request_id="r1",
    )


@pytest.fixture
def plugin() -> Crypt4gh:
    """Create the plugin with the default settings and an image.

    Returns:
        Plugin.
    """
    return Crypt4gh(PLUGIN_ID, Crypt4ghConfig(image=IMAGE))


@pytest.fixture
def host() -> Iterator[PluginHost]:
    """Create a plugin host that loads the installed plugin.

    Yields:
        Host whose default pipeline runs the plugin in its `request` phase.
    """
    conf = PluginsConfig.model_validate(
        {
            "available": {PLUGIN_ID: {"config": CONFIG}},
            "pipeline": [{"id": "decrypt", "alternatives": [{"plugin": PLUGIN_ID}]}],
        }
    )
    host = PluginHost(conf, api="tes")
    yield host
    host.close()


def test_entry_point_is_registered() -> None:
    """The plugin ID refers to the plugin class."""
    (entry_point,) = entry_points(group="foca.plugins", name=PLUGIN_ID)
    assert entry_point.load() is Crypt4gh


def test_plugin_class_is_valid() -> None:
    """FOCA accepts the class for TES, in the `request` phase."""
    assert check_plugin_class(Crypt4gh, PLUGIN_ID, "tes") is Crypt4gh
    assert Crypt4gh.phases() == ("request",)
    title, description = Crypt4gh.summary()
    assert title == "Decrypt Crypt4GH inputs in an extra executor."
    assert description.startswith("Adds an executor")


def test_settings_have_defaults() -> None:
    """All settings but the image have defaults."""
    config = Crypt4ghConfig(image=IMAGE)
    assert config.command == ("crypt4gh-decrypt",)
    assert config.volume == VOLUME
    assert config.encrypted_suffixes == (".c4gh",)
    assert config.key_suffixes == (".sec",)
    assert config.teardown is True
    assert config.teardown_on_error is False


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"image": ""},
        {"image": "with space"},
        {"image": IMAGE, "command": []},
        {"image": IMAGE, "command": [""]},
        {"image": IMAGE, "volume": "vol"},
        {"image": IMAGE, "volume": "/"},
        {"image": IMAGE, "volume": "/vol/"},
        {"image": IMAGE, "volume": "/vol//c"},
        {"image": IMAGE, "volume": "//vol/c"},
        {"image": IMAGE, "volume": "/vol/./c"},
        {"image": IMAGE, "volume": "/vol/../c"},
        {"image": IMAGE, "encrypted_suffixes": []},
        {"image": IMAGE, "encrypted_suffixes": ["c4gh"]},
        {"image": IMAGE, "key_suffixes": [".a/b"]},
        {"image": IMAGE, "key_suffixes": [".c4gh"]},
        {"image": IMAGE, "key_suffixes": [".key.c4gh"]},
        {"image": IMAGE, "encrypted_suffixes": [".x.sec"]},
        {"image": IMAGE, "other": "value"},
        {"image": IMAGE, "teardown": False, "teardown_on_error": True},
    ],
)
def test_settings_are_validated(data: dict[str, Any]) -> None:
    """Invalid or unknown settings are refused.

    Args:
        data: Invalid settings.
    """
    with pytest.raises(ValidationError):
        Crypt4ghConfig.model_validate(data)


def test_on_request_adds_decryption(plugin: Crypt4gh) -> None:
    """The decryption executor and volume are added; arguments are changed.

    Args:
        plugin: Plugin.
    """
    request = make_inbound(make_task())
    ctx = make_context()
    plugin.on_request(request, ctx)
    assert request.payload == make_task(
        executors=[
            decryption_executor(("/in/reads.fq.c4gh", "/vol/crypt4gh/reads.fq")),
            {
                "image": "aligner",
                "command": ["align", "/in/ref.fa", "/vol/crypt4gh/reads.fq", "/out"],
            },
            teardown_executor(),
        ],
        volumes=[VOLUME],
    )
    assert ctx.annotations == {"encrypted_inputs": 1, "key_inputs": 1}


def test_on_request_changes_stdin_and_keeps_volumes(plugin: Crypt4gh) -> None:
    """Standard input is changed too (also without command); volumes are kept.

    Args:
        plugin: Plugin.
    """
    task = make_task(
        executors=[
            {"image": "a", "command": ["wc"], "stdin": "/in/reads.fq.c4gh"},
            {"image": "b", "stdin": "/in/ref.fa"},
        ],
        volumes=["/scratch"],
    )
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    payload = request.payload
    assert payload["volumes"] == ["/scratch", VOLUME]
    assert payload["executors"][1]["stdin"] == "/vol/crypt4gh/reads.fq"
    assert payload["executors"][2]["stdin"] == "/in/ref.fa"


def test_on_request_uses_settings() -> None:
    """Image, command, volume and suffixes come from the settings."""
    plugin = Crypt4gh(
        PLUGIN_ID,
        Crypt4ghConfig(
            image="decrypt:1",
            command=("python3", "/app/decrypt.py"),
            volume="/data/plain",
            encrypted_suffixes=(".crypt4gh",),
            key_suffixes=(".key",),
        ),
    )
    task = {
        "inputs": [
            {"path": "/in/a.crypt4gh", "url": "s3://b/a"},
            {"path": "/in/me.key", "content": "key"},
            {"path": "/in/dir.crypt4gh", "url": "s3://b/d", "type": "DIRECTORY"},
        ],
        "executors": [{"image": "x", "command": ["cat", "/in/a.crypt4gh"]}],
    }
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    assert request.payload["executors"] == [
        {
            "image": "decrypt:1",
            "command": [
                *("python3", "/app/decrypt.py", "--key", "/in/me.key"),
                *("--file", "/in/a.crypt4gh", "/data/plain/a"),
            ],
        },
        {"image": "x", "command": ["cat", "/data/plain/a"]},
        {
            "image": "decrypt:1",
            "command": ["python3", "/app/decrypt.py", "--clear", "/data/plain"],
        },
    ]
    assert request.payload["volumes"] == ["/data/plain"]


def test_on_request_can_leave_out_teardown() -> None:
    """Without `teardown`, only the decryption executor is added."""
    plugin = Crypt4gh(PLUGIN_ID, Crypt4ghConfig(image=IMAGE, teardown=False))
    request = make_inbound(make_task())
    plugin.on_request(request, make_context())
    assert request.payload["executors"] == [
        decryption_executor(("/in/reads.fq.c4gh", "/vol/crypt4gh/reads.fq")),
        {
            "image": "aligner",
            "command": ["align", "/in/ref.fa", "/vol/crypt4gh/reads.fq", "/out"],
        },
    ]


def test_on_request_runs_teardown_after_errors() -> None:
    """With `teardown_on_error`, the task's own executors ignore errors.

    The decryption and teardown executors don't, and a second pass leaves
    the task as it is.
    """
    plugin = Crypt4gh(PLUGIN_ID, Crypt4ghConfig(image=IMAGE, teardown_on_error=True))
    task = make_task(
        executors=[
            {"image": "a", "command": ["true"], "ignore_error": False},
            {"image": "b", "command": ["cat", "/in/reads.fq.c4gh"]},
            "not an executor",
        ]
    )
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    executors = request.payload["executors"]
    assert executors[0] == decryption_executor(
        ("/in/reads.fq.c4gh", "/vol/crypt4gh/reads.fq")
    )
    assert executors[1] == {"image": "a", "command": ["true"], "ignore_error": True}
    assert executors[2] == {
        "image": "b",
        "command": ["cat", "/vol/crypt4gh/reads.fq"],
        "ignore_error": True,
    }
    assert executors[3] == "not an executor"
    assert executors[4] == teardown_executor()
    once = copy.deepcopy(request.payload)
    again = make_inbound(copy.deepcopy(once))
    plugin.on_request(again, make_context())
    assert again.payload == once


def test_on_request_moves_teardown_to_the_end(plugin: Crypt4gh) -> None:
    """Exact copies of the teardown executor among the task's own are dropped.

    The teardown runs once, after all of the task's own executors, also if
    the task names it before them. Executors that differ from it in any
    field (another volume, `ignore_error`) are the task's own and are kept.

    Args:
        plugin: Plugin.
    """
    early = teardown_executor()
    other_volume = {"image": IMAGE, "command": ["crypt4gh-decrypt", "--clear", "/x"]}
    near_copy = {**early, "ignore_error": False}
    task = make_task(
        executors=[early, {"image": "a", "command": ["true"]}, other_volume, near_copy]
    )
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    assert request.payload["executors"][1:] == [
        {"image": "a", "command": ["true"]},
        other_volume,
        near_copy,
        early,
    ]


@pytest.mark.parametrize(
    "task",
    [
        {"executors": [{"image": "alpine", "command": ["true"]}]},
        make_task(inputs=[{"url": "s3://b/a.sec", "path": "/keys/a.sec"}]),
        make_task(inputs=[{"url": "s3://b/d", "path": "/d.c4gh", "type": "DIRECTORY"}]),
        make_task(inputs=[{"url": "s3://b/x"}]),
    ],
)
def test_on_request_keeps_tasks_without_crypt4gh_inputs(
    plugin: Crypt4gh, task: dict[str, Any]
) -> None:
    """Tasks without Crypt4GH input files are unchanged.

    Args:
        plugin: Plugin.
        task: Task.
    """
    request = make_inbound(task)
    ctx = make_context()
    plugin.on_request(request, ctx)
    assert request.payload == task
    assert ctx.annotations["encrypted_inputs"] == 0


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"inputs": [{"url": "s3://b/a", "path": "/in/a.c4gh"}]},
            r"^inputs: Crypt4GH files need a secret key",
        ),
        (
            {"outputs": [{"url": "s3://b/o", "path": "/vol/crypt4gh/reads.fq"}]},
            r"^outputs\[0\]: path is reserved for decrypted files$",
        ),
        (
            {"outputs": [{"url": "s3://b/o", "path": "/in/reads.fq.c4gh"}]},
            r"^outputs\[0\]: path is that of a Crypt4GH input",
        ),
        (
            {"volumes": ["/scratch", "/vol/crypt4gh"]},
            r"^volumes\[1\]: path is reserved for decrypted files$",
        ),
        (
            {"volumes": ["/vol"]},
            r"^volumes\[0\]: path is reserved for decrypted files$",
        ),
        (
            {"outputs": [{"url": "s3://b/o", "path": "/"}]},
            r"^outputs\[0\]: path is reserved for decrypted files$",
        ),
        (
            {"outputs": [{"url": "s3://b/o", "path": "/in/./reads.fq.c4gh"}]},
            r"^outputs\[0\]: path is that of a Crypt4GH input",
        ),
        (
            {"outputs": [{"url": "s3://b/o", "path": "//in/reads.fq.c4gh"}]},
            r"^outputs\[0\]: path is that of a Crypt4GH input",
        ),
    ],
)
def test_on_request_rejects(
    plugin: Crypt4gh, fields: dict[str, Any], message: str
) -> None:
    """Tasks whose Crypt4GH inputs can't be decrypted are rejected (422).

    Args:
        plugin: Plugin.
        fields: Fields of the task.
        message: Pattern of the message.
    """
    with pytest.raises(Rejected, match=message) as raised:
        plugin.on_request(make_inbound(make_task(**fields)), make_context())
    assert raised.value.status == 422


def test_on_request_rejects_inputs_in_volume(plugin: Crypt4gh) -> None:
    """Inputs in the volume are rejected.

    Args:
        plugin: Plugin.
    """
    task = make_task()
    task["inputs"].append({"url": "s3://b/x", "path": "/vol/crypt4gh/x"})
    with pytest.raises(Rejected, match=r"^inputs\[3\]: path is reserved"):
        plugin.on_request(make_inbound(task), make_context())


def test_on_request_refuses_other_payloads(plugin: Crypt4gh) -> None:
    """Payloads that are no JSON objects are not applicable.

    Args:
        plugin: Plugin.
    """
    with pytest.raises(NotApplicable):
        plugin.on_request(make_inbound([]), make_context())


def test_on_request_takes_malformed_inputs_as_absent(plugin: Crypt4gh) -> None:
    """Inputs that are no list have no Crypt4GH files; the task is unchanged.

    Args:
        plugin: Plugin.
    """
    task = make_task(inputs="x", outputs=1, volumes={})
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    assert request.payload == task


def test_on_request_keeps_malformed_items(plugin: Crypt4gh) -> None:
    """Items of the wrong type are kept as they are, around the rewrite.

    Args:
        plugin: Plugin.
    """
    task = make_task(
        outputs=["o", {"url": "s3://b/o", "path": ["/in/reads.fq.c4gh"]}],
        volumes=None,
        executors=["e", {"image": "a", "command": [1, "/in/reads.fq.c4gh"]}],
    )
    task["inputs"].append("i")
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    payload = request.payload
    assert payload["executors"][1:] == [
        "e",
        {"image": "a", "command": [1, "/vol/crypt4gh/reads.fq"]},
        teardown_executor(),
    ]
    assert payload["volumes"] == [VOLUME]
    assert payload["outputs"] == task["outputs"]


@pytest.mark.parametrize(
    "fields",
    [
        {"executors": "e"},
        {"executors": None},
        {"executors": []},
        {"volumes": "v"},
        {"volumes": {}},
    ],
)
def test_on_request_keeps_tasks_with_malformed_lists(
    plugin: Crypt4gh, fields: dict[str, Any]
) -> None:
    """Tasks with empty executors, or lists of another type, are left alone.

    Args:
        plugin: Plugin.
        fields: Field of the wrong type.
    """
    request = make_inbound(make_task(**fields))
    plugin.on_request(request, make_context())
    assert request.payload == make_task(**fields)


def test_on_request_changes_aliases_of_inputs(plugin: Crypt4gh) -> None:
    """Arguments and stdin that name an input in another spelling are changed.

    Args:
        plugin: Plugin.
    """
    task = make_task(
        executors=[
            {
                "image": "a",
                "command": ["cat", "/in/./reads.fq.c4gh", "//in/reads.fq.c4gh"],
                "stdin": "/in/x/../reads.fq.c4gh",
            },
            {"image": "b", "command": ["ls", "in/reads.fq.c4gh"]},
        ]
    )
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    first, second = request.payload["executors"][1:-1]
    assert first["command"] == ["cat", *(["/vol/crypt4gh/reads.fq"] * 2)]
    assert first["stdin"] == "/vol/crypt4gh/reads.fq"
    assert second["command"] == ["ls", "in/reads.fq.c4gh"]


def test_on_request_keeps_tasks_without_executors(plugin: Crypt4gh) -> None:
    """Tasks without executors are left for the app.

    Args:
        plugin: Plugin.
    """
    task = make_task()
    del task["executors"]
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    assert "executors" not in request.payload


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"outputs": [{"url": "s3://b/o", "path": "/in/reads.fq.c4gh"}]},
            r"^outputs\[1\]: path is that of a Crypt4GH input",
        ),
        (
            {"outputs": [{"url": "s3://b/o", "path": "/"}]},
            r"^outputs\[1\]: path is reserved for decrypted files$",
        ),
        (
            {"volumes": ["/vol"]},
            r"^volumes\[1\]: path is reserved for decrypted files$",
        ),
    ],
)
def test_on_request_checks_rewritten_tasks(
    plugin: Crypt4gh, fields: dict[str, Any], message: str
) -> None:
    """A task changed already is still checked.

    Args:
        plugin: Plugin.
        fields: Fields added to the changed task.
        message: Pattern of the expected message.
    """
    request = make_inbound(make_task())
    plugin.on_request(request, make_context())
    task = request.payload
    for field, value in fields.items():
        task[field] = [*task.get(field, []), *value]
    with pytest.raises(Rejected, match=message) as raised:
        plugin.on_request(make_inbound(task), make_context())
    assert raised.value.status == 422


def test_on_request_rewrites_tasks_with_other_decryption(plugin: Crypt4gh) -> None:
    """Tasks that resemble a changed one only in part are checked in full.

    A first executor with the decryption image and command but other
    arguments, or a second copy of the volume, isn't the plugin's change.

    Args:
        plugin: Plugin.
    """
    fake = {"image": IMAGE, "command": ["crypt4gh-decrypt"]}
    task = make_task(volumes=[VOLUME], executors=[fake])
    with pytest.raises(Rejected, match=r"^volumes\[0\]: path is reserved"):
        plugin.on_request(make_inbound(task), make_context())
    request = make_inbound(make_task())
    plugin.on_request(request, make_context())
    twice = request.payload
    twice["volumes"] = [VOLUME, VOLUME]
    with pytest.raises(Rejected, match=r"^volumes\[0\]: path is reserved"):
        plugin.on_request(make_inbound(twice), make_context())


@pytest.mark.parametrize(
    "fields",
    [
        {"outputs": [{"url": "s3://b/o", "path": ""}]},
        {"outputs": [{"url": "s3://b/o", "path": "vol/crypt4gh/reads.fq"}]},
        {"inputs": [*make_task()["inputs"], {"url": "s3://b/i", "path": "in/x"}]},
        {"volumes": ["vol/crypt4gh"]},
    ],
)
def test_on_request_rejects_relative_paths(
    plugin: Crypt4gh, fields: dict[str, Any]
) -> None:
    """Relative paths of inputs, outputs and volumes are rejected.

    Args:
        plugin: Plugin.
        fields: Fields of the task.
    """
    with pytest.raises(Rejected, match=r"\]: path must be absolute$"):
        plugin.on_request(make_inbound(make_task(**fields)), make_context())


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/v*/crypt4gh/*", "reserved for decrypted files"),
        ("/*", "reserved for decrypted files"),
        ("/vol/crypt4gh/*.fq", "reserved for decrypted files"),
        ("/in/*.c4gh", "that of a Crypt4GH input"),
        ("/in/reads.fq.c4g[h]", "that of a Crypt4GH input"),
    ],
)
def test_on_request_rejects_wildcard_outputs(
    plugin: Crypt4gh, path: str, message: str
) -> None:
    """Outputs with wildcards that may match the volume or an input are rejected.

    Args:
        plugin: Plugin.
        path: Output path with wildcards.
        message: Part of the expected message.
    """
    task = make_task(outputs=[{"url": "s3://b/o", "path": path}])
    with pytest.raises(Rejected, match=rf"^outputs\[0\]: path is {message}"):
        plugin.on_request(make_inbound(task), make_context())


def test_on_request_rejects_outputs_of_inputs_with_brackets(plugin: Crypt4gh) -> None:
    """An output at the path of an input whose name has `[` is rejected.

    Args:
        plugin: Plugin.
    """
    path = "/in/a[1].c4gh"
    task = make_task(outputs=[{"url": "s3://b/o", "path": path}])
    task["inputs"][0]["path"] = path
    with pytest.raises(Rejected, match=r"^outputs\[0\]: path is that of a Crypt4GH"):
        plugin.on_request(make_inbound(task), make_context())


def test_on_request_keeps_wildcard_outputs_elsewhere(plugin: Crypt4gh) -> None:
    """Outputs with wildcards outside the volume and inputs are kept.

    Args:
        plugin: Plugin.
    """
    outputs = [{"url": "s3://b/o", "path": "/out/*.bam"}]
    request = make_inbound(make_task(outputs=outputs))
    plugin.on_request(request, make_context())
    assert request.payload["outputs"] == outputs


def test_on_request_takes_null_type_for_file(plugin: Crypt4gh) -> None:
    """Inputs with a null type are files.

    Args:
        plugin: Plugin.
    """
    task = make_task()
    task["inputs"][0]["type"] = None
    request = make_inbound(task)
    plugin.on_request(request, make_context())
    assert request.payload["volumes"] == [VOLUME]


def test_on_request_is_idempotent(plugin: Crypt4gh) -> None:
    """A task that the plugin changed already is left as it is.

    Args:
        plugin: Plugin.
    """
    request = make_inbound(make_task())
    plugin.on_request(request, make_context())
    once = copy.deepcopy(request.payload)
    again = make_inbound(copy.deepcopy(once))
    ctx = make_context()
    plugin.on_request(again, ctx)
    assert again.payload == once
    assert ctx.annotations["encrypted_inputs"] == 1


def test_on_request_changes_arguments_of_rewritten_tasks(plugin: Crypt4gh) -> None:
    """Executors of a changed task that still name a Crypt4GH input are changed.

    Args:
        plugin: Plugin.
    """
    request = make_inbound(make_task())
    plugin.on_request(request, make_context())
    task = copy.deepcopy(request.payload)
    task["executors"].append(
        {
            "image": "b",
            "command": ["cat", "/in/reads.fq.c4gh"],
            "stdin": "/in/reads.fq.c4gh",
        }
    )
    again = make_inbound(task)
    plugin.on_request(again, make_context())
    executors = again.payload["executors"]
    assert len(executors) == 4
    assert executors[0] == request.payload["executors"][0]
    assert executors[2]["command"] == ["cat", "/vol/crypt4gh/reads.fq"]
    assert executors[2]["stdin"] == "/vol/crypt4gh/reads.fq"
    # The teardown moves after the added executor
    assert executors[3] == teardown_executor()
    assert again.payload["volumes"] == [VOLUME]


@pytest.mark.parametrize(
    "fields",
    [
        {"executors": None},
        {"executors": []},
        {"volumes": "v"},
    ],
)
def test_on_request_keeps_malformed_tasks_with_bad_inputs(
    plugin: Crypt4gh, fields: dict[str, Any]
) -> None:
    """Malformed tasks are left for the app, even if their inputs are bad too.

    Args:
        plugin: Plugin.
        fields: Field of the wrong type.
    """
    inputs = [{"url": "s3://b/a", "path": "/in/./a.c4gh"}]
    request = make_inbound(make_task(inputs=inputs, **fields))
    plugin.on_request(request, make_context())
    assert request.payload == make_task(inputs=inputs, **fields)


def test_plugin_meets_contract() -> None:
    """The plugin passes FOCA's contract checks (class, metadata, hooks)."""
    check_plugin_contract(
        Crypt4gh,
        config=CONFIG,
        plugin_id=PLUGIN_ID,
        inbound=make_inbound(make_task()),
    )


def test_harness_runs_hook() -> None:
    """Through the engine, the hook changes the task and records counts."""
    harness = PluginHarness(Crypt4gh, config=CONFIG, params={}, plugin_id=PLUGIN_ID)
    payload = harness.run_request(make_inbound(make_task())).payload
    assert payload["executors"][0] == decryption_executor(
        ("/in/reads.fq.c4gh", "/vol/crypt4gh/reads.fq")
    )
    assert [(e.phase, e.outcome, e.annotations) for e in harness.executions] == [
        ("request", "ok", {"encrypted_inputs": 1, "key_inputs": 1}),
    ]


def test_pipeline_runs_plugin(host: PluginHost) -> None:
    """The engine loads the installed plugin and runs its hook.

    Args:
        host: Plugin host.
    """
    run = host.start(
        make_inbound(make_task()), Principal(subject="alice"), request_id="r1"
    )
    assert run.request().payload["volumes"] == [VOLUME]
    assert [(e.phase, e.outcome) for e in run.executions] == [("request", "ok")]
