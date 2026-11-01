<!-- markdownlint-disable MD033 MD041 -- logo first; <picture> for dark mode -->
<a href="https://elixir-on.cloud/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo_elixir_on_cloud_night.png">
    <img src="docs/images/logo_elixir_on_cloud.png" alt="ELIXIR On-Cloud">
  </picture>
</a>
<!-- markdownlint-enable MD033 MD041 -->

# FOCA plugins

[![License][badge-license]][badge-url-license]
[![Build_status][badge-build-status]][badge-url-build-status]
[![Docs][badge-docs]][badge-url-docs]
[![Coverage][badge-coverage]][badge-url-coverage]

<!-- --8<-- [start:synopsis] -->
Plugins for gateway services built with
[FOCA](https://github.com/elixir-cloud-aai/foca), such as
[proTES](https://github.com/elixir-cloud-aai/proTES) (GA4GH TES) and, later,
[proWES](https://github.com/elixir-cloud-aai/proWES) (GA4GH WES), brought to
you by [ELIXIR On-Cloud](https://elixir-on.cloud/). Gateways accept requests
from clients and forward them to backends; plugins process these requests
on the way: they change tasks (e.g., add inputs or executors), reject them
by policy, choose and order the backends, and adapt each forwarded request.

FOCA's plugin engine (`foca.plugins`) loads plugins from installed Python
distributions (entry point group `foca.plugins`) and runs them in the
pipelines that operators configure; the same plugins serve every gateway
app. This repository holds the plugins of ELIXIR On-Cloud: one distribution
per plugin (`foca-plugin-<group>-<subgroup>-<name>`), grouped by the gateway
API (`generic`, `tes`, `wes`) and by function (e.g., `routing`, `policy`),
plus the meta distribution `foca-plugins`, whose extras install a group or
subgroup of plugin versions tested together. Plugins from other
repositories work the same way.
<!-- --8<-- [end:synopsis] -->

<!-- --8<-- [start:status] -->
**Status:** the plugin API is new in FOCA 0.18.0; the plugins support
FOCA 0.18 and 0.19. Plugins so far, both experimental and released as
0.1.x: `generic.routing.random` and
`tes.routing.distance`, plus the example `generic.example.noop`, the
template of new plugins (never published). The first release train is
`foca-plugins` 0.1.0.
<!-- --8<-- [end:status] -->

**Read the [documentation][docs]** for installing plugins, writing them,
the plugin catalogue, contributing and releasing.

## Installation

<!-- --8<-- [start:install] -->
Install plugins into the environment of the gateway app, usually in an
image derived from the app's image, then enable them in the app's
configuration (FOCA's `plugins` section): installed is not enabled. Either
a group or subgroup of a release train, as an extra of the meta
distribution `foca-plugins` (e.g., `foca-plugins[tes]==0.1.0`), or single
plugin distributions (`foca-plugin-<group>-<subgroup>-<name>`).
<!-- --8<-- [end:install] -->

See [Installing][docs-installing] for the extras, single distributions and
the derived-image recipe.

## Layout

<!-- --8<-- [start:layout] -->
```text
plugins/<group>/<subgroup>/<name>/  one distribution per plugin:
  pyproject.toml                    foca-plugin-<group>-<subgroup>-<name>
  src/foca_plugins/<group>/<subgroup>/<name>/
  tests/, README.md, CHANGELOG.md, LICENSE
meta/                               the meta distribution foca-plugins (extras only)
scripts/                            change detection, checks, new plugins
docs/                               this documentation (MkDocs)
pyproject.toml                      uv workspace root: tool configuration, dev groups
```

| Item | Convention |
| --- | --- |
| Plugin ID (entry point name) | `<group>.<subgroup>.<name>`, e.g., `tes.routing.distance` |
| Distribution | `foca-plugin-<group>-<subgroup>-<name>` |
| Import path | `foca_plugins.<group>.<subgroup>.<name>` (with `_` for `-`) |
| Entry point group | `foca.plugins` |
| Release tag | `<distribution>-v<version>` |

`foca_plugins`, `foca_plugins.<group>` and `foca_plugins.<group>.<subgroup>`
are namespace packages (PEP 420, no `__init__.py`), shared by all plugin
distributions.
<!-- --8<-- [end:layout] -->

## Citation

<!-- --8<-- [start:citation] -->
If you use FOCA plugins, please cite them with the metadata in
[`CITATION.cff`](https://github.com/elixir-cloud-aai/foca-plugins/blob/dev/CITATION.cff):
on GitHub, "Cite this repository" in the sidebar of the repository formats it
as APA or BibTeX.
<!-- --8<-- [end:citation] -->

## Contributing

Contributions are welcome! See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[Contributing][docs-contributing] in the documentation, and mind our [code of
conduct](CODE_OF_CONDUCT.md). To start a plugin, run
`python scripts/new_plugin.py <group> <subgroup> <name>` (see [Writing a
plugin][docs-writing]).

[badge-build-status]: <https://github.com/elixir-cloud-aai/foca-plugins/actions/workflows/ci.yml/badge.svg?branch=dev>
[badge-coverage]: <https://codecov.io/gh/elixir-cloud-aai/foca-plugins/branch/dev/graph/badge.svg?branch=dev>
[badge-docs]: <https://img.shields.io/readthedocs/foca-plugins>
[badge-license]: <https://img.shields.io/badge/license-Apache%202.0-blue.svg>
[badge-url-build-status]: <https://github.com/elixir-cloud-aai/foca-plugins/actions/workflows/ci.yml?query=branch%3Adev>
[badge-url-coverage]: <https://codecov.io/gh/elixir-cloud-aai/foca-plugins?branch=dev>
[badge-url-docs]: <https://foca-plugins.readthedocs.io/en/latest/>
[badge-url-license]: <https://www.apache.org/licenses/LICENSE-2.0>
[docs]: <https://foca-plugins.readthedocs.io/en/latest/>
[docs-contributing]: <https://foca-plugins.readthedocs.io/en/latest/contributing/>
[docs-installing]: <https://foca-plugins.readthedocs.io/en/latest/installing/>
[docs-writing]: <https://foca-plugins.readthedocs.io/en/latest/writing-plugins/>
