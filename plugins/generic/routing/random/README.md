# generic.routing.random

<!-- --8<-- [start:summary] -->
Routes requests to the candidate backends in random order, so that they
spread over all backends without round-robin state. The successor of
proTES's random task distribution.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `generic.routing.random` |
| Distribution | `foca-plugin-generic-routing-random` |
| Import path | `foca_plugins.generic.routing.random` |
| APIs | all (`*`) |
| Phases | `route` |
| Maturity | experimental |
| Data transfers | none |
| Data stored | none (no plugin store) |
| FOCA | 0.18, 0.19 (`>=0.18.0,<0.20`) |

## What it does

- `route`: returns all candidate backends, shuffled with the operating
  system's randomness (`random.SystemRandom`). It drops no candidate and
  adds none; the gateway then tries them in that order. It records the
  annotations `candidates` (their number) and `seeded` (whether a seed was
  used).

It never rejects a request and never raises `NotApplicable`. The order
itself is in the provenance of each request (`route.candidates_out`), so
the plugin doesn't record it again.

FOCA's default router, `foca.routing.ordered`, keeps the order of the
backend list and shuffles only the backends of each service registry among
themselves. This plugin shuffles all candidates, configured or discovered.

## Configuration

Admin settings, in the gateway app's configuration (FOCA's `plugins`
section):

```yaml
plugins:
  available:
    generic.routing.random:
      distribution: foca-plugin-generic-routing-random
      config:
        allow_seed: false  # default; true only for tests
  pipeline:
    - id: route
      alternatives:
        - plugin: generic.routing.random
```

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `config.allow_seed` | boolean | `false` | whether pipelines may set `seed` |
| `params.seed` | integer or `null` | `null` | seed of the shuffling, for reproducible orders in tests; refused (when pipelines are resolved) unless `allow_seed` is set |

With a seed, the order is a function of the seed and the candidate list,
on the same Python version (Python doesn't promise the same shuffles
across versions). Seeded orders are predictable, also for clients that
choose the plugin in their own pipelines: keep `allow_seed` off in
production. A test setup:

```yaml
plugins:
  available:
    generic.routing.random:
      config:
        allow_seed: true  # tests only
  pipeline:
    - id: route
      alternatives:
        - plugin: generic.routing.random
          params:
            seed: 42
```

### proTES's old default

proTES used to try distance-based routing first and fall back to random
routing. The same pipeline with plugins, once distance routing
([`tes.routing.distance`](https://github.com/elixir-cloud-aai/foca-plugins/issues/8))
is released:

```yaml
plugins:
  pipeline:
    - id: route
      alternatives:
        - plugin: tes.routing.distance
        - plugin: generic.routing.random
```

## Installation

```bash
pip install foca-plugin-generic-routing-random
```

See "Installing" in the docs for release trains and derived images.

## Development

From the repository root, with the development environment set up (see
"Contributing" in the docs):

```bash
uv run ruff check plugins/generic/routing/random
uv run python -m scripts.packages mypy plugins/generic/routing/random
uv run coverage run --source plugins/generic/routing/random/src \
  -m pytest plugins/generic/routing/random
uv run coverage report
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
