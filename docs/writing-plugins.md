# Writing a plugin

The plugin API (the `Plugin` base class, the hooks, the models, the
exceptions, the context) is FOCA's: read FOCA's [plugins
page](https://foca.readthedocs.io/en/latest/plugins/) (`docs/plugins.md` in
the FOCA repository) first. This page describes the conventions of this
repository; the example plugin
[`generic.example.noop`](https://github.com/elixir-cloud-aai/foca-plugins/tree/dev/plugins/generic/example/noop)
shows them all and is the template of new plugins.

## Start from the template

Open an issue for the plugin first (what it does, its phases, its data
transfers), then create it from the template:

```bash
python scripts/new_plugin.py tes routing distance
uv sync  # adds the plugin to `uv.lock` and installs it
```

`scripts/new_plugin.py <group> <subgroup> <name>` copies
`plugins/generic/example/noop/` to `plugins/<group>/<subgroup>/<name>/`,
renames the plugin ID, the distribution, the import path and the class
(e.g., `Distance`, with `DistanceConfig` and `DistanceParams`), removes the
lines that keep the example off PyPI, and writes a new README and changelog
with TODOs. The copy passes the checks at once; then implement the plugin
and fill in the TODOs. Commit `uv.lock` with it.

## Names

| Item | Convention | Example |
| --- | --- | --- |
| Directory | `plugins/<group>/<subgroup>/<name>/` | `plugins/tes/routing/distance/` |
| Plugin ID (entry point name) | `<group>.<subgroup>.<name>` | `tes.routing.distance` |
| Distribution | `foca-plugin-<group>-<subgroup>-<name>` | `foca-plugin-tes-routing-distance` |
| Import path | `foca_plugins.<group>.<subgroup>.<name>` (`_` for `-`) | `foca_plugins.tes.routing.distance` |
| Entry point group | `foca.plugins` | |
| Release tag | `<distribution>-v<version>` | `foca-plugin-tes-routing-distance-v1.2.0` |

- **Groups** follow the gateway API: `generic` (any API, `apis =
  frozenset({"*"})`), `tes`, `wes`. **Subgroups** name the function, e.g.,
  `routing`, `policy`, `data`, `identity` (see `plugins/README.md`).
  Subgroups and names are lowercase, with digits and single hyphens, and
  have no part like `v1` (it would make release tags ambiguous). The
  distribution names of all plugins must differ (e.g., not both
  `tes/a-b/c` and `tes/a/b-c`).
- Unprefixed IDs (`<group>.<subgroup>.<name>`) are reserved for this
  repository; plugins of other repositories use a prefix of their own,
  e.g., `org.example.tes.routing.cost`. FOCA's built-in plugins are
  `foca.<subgroup>.<name>`, those of apps, e.g., `protes.<subgroup>.<name>`.
- `foca_plugins`, `foca_plugins.<group>` and `foca_plugins.<group>.<subgroup>`
  are **namespace packages** ([PEP 420](https://peps.python.org/pep-0420/)):
  never add an `__init__.py` to them, or installed plugins hide each other.
  The plugin's own package (`.../<name>/`) has one, and a `py.typed`.

`python -m scripts.checks check` (a pre-commit hook and part of CI's
`lint` job) checks these conventions, and that each package ships the
repository's `LICENSE`.

## Package contents

```text
plugins/<group>/<subgroup>/<name>/
  pyproject.toml   metadata, dependencies, entry point; static version
  src/foca_plugins/<group>/<subgroup>/<name>/__init__.py, py.typed
  tests/           unit tests and a pipeline test through FOCA's engine
  README.md        ID, phases, maturity, data transfers, configuration
  CHANGELOG.md     per version, in the sections of the release notes
  LICENSE          a copy of the repository's LICENSE
```

- **Dependencies:** `foca` with the supported range (`>=X,<Y`), plus what
  the plugin imports itself (e.g., `pydantic`). Keep them few: every
  dependency ends up in the gateway's image. Development tools are shared,
  in the root `pyproject.toml` (dependency group `dev`); the tool
  configuration (ruff, mypy, pytest, coverage) is shared there, too.
- **README:** keep the summary between the `summary` markers: the [plugin
  catalogue](catalogue.md) shows it. Document every setting (`Config`) and
  parameter (`Params`), the phases, what the plugin records (annotations),
  and its data transfers to third parties.
- **Metadata of the class:** `apis`, `maturity` (`experimental` until it has
  proven itself, then `stable`; a string in the class body, which the checks
  read to place a released plugin in the meta distribution's `[all]` or
  `[experimental]`), `capabilities` (generic descriptors, not package names)
  and `data_transfers`.

## Rules for plugin code

In addition to FOCA's rules (plain data and an explicit context; one
instance serves all requests from several threads; idempotent `setup()`):

- **No web framework:** don't import Flask, Werkzeug, Connexion or
  Starlette, nor FOCA's framework glue `foca.plugins.adapter`. The ast-grep
  rule `no-framework-imports` refuses them in `plugins/*/*/*/src/`, so that
  plugins run unchanged on FOCA 1.0.
- **No personal data in annotations** (`ctx.record()`); keep them small.
- **Outbound calls** go through `ctx.http` (the gateway's guarded client)
  where the app provides one, within `ctx.remaining()`; never disable TLS
  verification (a semgrep rule refuses it).
- **Settings models** forbid unknown fields (`extra="forbid"`), so that
  typos in configurations fail at startup.

## Tests and checks

Every package keeps 100% coverage of its `src/`. Run the checks of one
plugin from the repository root, as CI does:

```bash
uv run ruff check plugins/tes/routing/distance
uv run ruff format --check plugins/tes/routing/distance
uv run ast-grep scan plugins/tes/routing/distance
uv run python -m scripts.packages mypy plugins/tes/routing/distance
uv run coverage run --source plugins/tes/routing/distance/src \
  -m pytest plugins/tes/routing/distance
uv run coverage report
```

The template's tests show the kinds of tests that every plugin keeps:

- unit tests that call the hooks with a `Context` of their own;
- FOCA's contract checks (`check_plugin_contract` of FOCA's plugin test
  kit, `foca.plugins.testing`): the class, its metadata and settings
  models, no framework imports, and hook calls through the engine, once and
  from several threads. The template passes its admin settings only; pass
  sample inputs (`inbound=`, `candidates=`, `response=`, `http=`) that make
  your plugin's hooks do their work, and `api=` for plugins of other APIs
  than TES;
- tests through the engine with the kit's `PluginHarness` (and its
  `FakeHttp` for `ctx.http`, its builders such as `make_inbound()` and
  `make_backend()`);
- a pipeline test that loads the plugin through its installed entry point
  with FOCA's `PluginHost`, as gateway apps do.

See "Testing plugins" on FOCA's [plugins
page](https://foca.readthedocs.io/en/latest/plugins/#testing-plugins).

## Compatibility

CI tests each plugin with the lowest and highest Python versions that FOCA
supports and the default one (3.11, 3.13, 3.15), and with the lowest
supported FOCA version, 0.18.0, the first release with the plugin API (the
lower bound of every plugin, `foca>=0.18.0`), and FOCA's `dev` branch, from
Git, so that changes of the plugin API show before FOCA releases them. The
development environment has the newest FOCA release (`uv.lock`).

## Changes and releases

Commit messages and pull request titles use the plugin's path as their
scope, e.g., `feat(tes/routing/distance): add region filter`. Add each
user-visible change to the plugin's `CHANGELOG.md` under `## Unreleased`.
See [Releasing](releasing.md).
