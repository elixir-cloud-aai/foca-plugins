# Installing

Plugins are Python distributions. A gateway app loads the plugins that are
installed in its environment **and** listed in its configuration (FOCA's
`plugins.available`; see FOCA's [plugins
page](https://foca.readthedocs.io/en/latest/plugins/)): installed is not
enabled. Installing a whole group and enabling two plugins is valid;
security-conscious operators install only what they enable, to keep
dependencies (and scanner findings) out of their images.

!!! note "Experimental plugins only"

    The plugins released so far, `generic.routing.random` and
    `tes.routing.distance` (0.1.1 each), are experimental; the first release
    train is `foca-plugins` 0.1.0. Distributions named below that don't
    exist yet are examples of the naming scheme.

## Release trains: the meta distribution

The meta distribution `foca-plugins` has no code, only extras that pin
plugins of this repository to exact versions. Each release of it is a
**release train** (a semantic version, set by the changes of its pins; see
[Releasing](releasing.md#versions)): plugins tested together against the
FOCA versions that it declares.

| Extra | Installs | Now |
| --- | --- | --- |
| `foca-plugins[<group>]` (`generic`, `tes`, `wes`) | the stable and experimental plugins of a group | `[generic]`: `generic.routing.random`; `[tes]`: `tes.routing.distance`; `[wes]`: empty |
| `foca-plugins[<group>-<subgroup>]`, e.g., `[tes-routing]` | those of a subgroup | `[generic-routing]`, `[tes-routing]` (a subgroup's extra is added with its first plugin) |
| `foca-plugins[all]` | all stable plugins | empty |
| `foca-plugins[experimental]` | all experimental plugins | both plugins |

For example:

```bash
pip install "foca-plugins[tes-routing]==0.1.0"
```

The example plugin `generic.example.noop` is never published, so it is in
no extra.

## Single distributions

Install plugins directly to choose versions yourself, or to combine plugins
of this repository with those of others, e.g. (the second name is an
example):

```bash
pip install "foca-plugin-tes-routing-distance==0.1.1" "org-example-cost-routing==<version>"
```

Each plugin declares the FOCA versions that it supports (`foca>=X,<Y`) and
its plugin API version; FOCA refuses plugins of another API version at
startup.

## A derived image (recommended)

Add plugins to a gateway app by building an image derived from the app's
image. This is explicit, reviewable and hash-pinned, and works for plugins
of any repository. proTES documents its image and configuration in its
repository; the same recipe applies to every gateway app:

```dockerfile
# The app's release image, by digest
FROM docker.io/elixircloud/protes:<version>@sha256:<digest>
COPY plugins.txt /tmp/plugins.txt
RUN pip install --no-cache-dir --no-deps --require-hashes -r /tmp/plugins.txt
```

`plugins.txt` lists the plugins and their dependencies that the app's image
doesn't have, each pinned with hashes, e.g., generated with `uv pip
compile` or `pip-compile --generate-hashes` against the app's own
requirements:

```text
foca-plugin-tes-routing-distance==<version> \
    --hash=sha256:<hash of the wheel>
```

Then enable the plugins in the app's configuration, e.g., in a Helm values
file of the app's chart, which selects the derived image
(`image.repository`, `image.tag`) and renders the `plugins` section; the
chart never installs code. Scan and sign the derived image as you do the
app's image.

Not recommended: installing plugins at container start (e.g., in an init
container): it is unpinned and unreviewed, and breaks read-only root file
systems.

## Verifying distributions

Every wheel and sdist is published with [PEP 740
attestations](https://peps.python.org/pep-0740/) (Sigstore) by the release
workflow of this repository, and with GitHub artifact attestations (SLSA
build provenance). pip does not verify attestations yet; verify them before
building an image, e.g.:

```bash
gh attestation verify foca_plugin_tes_routing_distance-<version>-py3-none-any.whl \
  --repo elixir-cloud-aai/foca-plugins
```
