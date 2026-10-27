# generic.example.noop

<!-- --8<-- [start:summary] -->
Example plugin that changes nothing: it leaves inbound requests and the
candidate backends as they are and records what it saw. It is the template
of new plugins (`python scripts/new_plugin.py <group> <subgroup> <name>`)
and is **not published**.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `generic.example.noop` |
| Distribution | `foca-plugin-generic-example-noop` (not on PyPI) |
| Import path | `foca_plugins.generic.example.noop` |
| APIs | all (`*`) |
| Phases | `request`, `route` |
| Maturity | experimental |
| Data transfers | none |
| FOCA | 0.18 (`>=0.18.0,<0.19`) |

## What it does

- `request`: leaves the request unchanged; records the annotation `label`
  (the `label` of its settings).
- `route`: keeps all candidate backends, in their order; records the
  annotation `candidates` (their number).

It never fails or rejects a request.

## Configuration

Admin settings, in the gateway app's configuration (FOCA's `plugins`
section):

```yaml
plugins:
  available:
    generic.example.noop:
      distribution: foca-plugin-generic-example-noop
      config:
        label: gateway-1  # default: noop; 1 to 64 characters
  pipeline:
    - id: noop
      alternatives:
        - plugin: generic.example.noop
          params:
            record: true  # default: true; false records no annotations
```

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `config.label` | string, 1 to 64 characters | `noop` | value of the annotation `label` |
| `params.record` | boolean | `true` | whether to record annotations |

## Installation

The example is not published. To try it, install it from a checkout of
this repository into the environment of the gateway app:

```bash
pip install ./plugins/generic/example/noop
```

## Development

From the repository root, with the development environment set up (see
"Contributing" in the docs):

```bash
uv run ruff check plugins/generic/example/noop
uv run python -m scripts.packages mypy plugins/generic/example/noop
uv run coverage run --source plugins/generic/example/noop/src \
  -m pytest plugins/generic/example/noop
uv run coverage report
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
