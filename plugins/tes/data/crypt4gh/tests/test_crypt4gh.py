"""Tests of the plugin `tes.data.crypt4gh`.

Unit tests call the hook with a context of their own; the other tests use
FOCA's plugin test kit (`foca.plugins.testing`): the contract checks, a
harness that runs the plugin through the real engine, and a pipeline that
loads the plugin through its installed entry point, as gateway apps do.
"""

from collections.abc import Iterator
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
        {"image": IMAGE, "volume": "/vol/./c"},
        {"image": IMAGE, "volume": "/vol/../c"},
        {"image": IMAGE, "encrypted_suffixes": []},
        {"image": IMAGE, "encrypted_suffixes": ["c4gh"]},
        {"image": IMAGE, "key_suffixes": [".a/b"]},
        {"image": IMAGE, "key_suffixes": [".c4gh"]},
        {"image": IMAGE, "other": "value"},
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
    ]
    assert request.payload["volumes"] == ["/data/plain"]


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
