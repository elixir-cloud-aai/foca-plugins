# foca-plugins

The meta distribution of the [FOCA gateway
plugins](https://github.com/elixir-cloud-aai/foca-plugins) of ELIXIR
On-Cloud: it has no code, only extras that install the plugins of a group
or subgroup, each pinned to an exact version. Each release is a release
train: a combination of plugins tested together against the FOCA versions
that it declares. Versions are calendar versions (`YYYY.MM.N`).

Extras: `[<group>]` (e.g., `[tes]`), `[<group>-<subgroup>]` (e.g.,
`[tes-routing]`), `[all]` (stable plugins) and `[experimental]`.

Installed is not enabled: a gateway app (e.g., proTES) loads only the
plugins of its configuration (`plugins.available`). To keep dependencies
and scanner findings out of images, install only the plugins that you
enable, e.g., single plugin distributions
(`foca-plugin-<group>-<subgroup>-<name>`). See
[Installing](https://foca-plugins.readthedocs.io/en/latest/installing/) in
the docs.

No plugins have been released yet, so all extras are empty.
