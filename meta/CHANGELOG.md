# Changelog of `foca-plugins`

Each release of the meta distribution is a release train, tagged
`foca-plugins-v<version>` with a semantic version, set by the changes of
its pins. Each lists the plugin versions that its extras pin and the FOCA
versions that it was tested with, then the changes in the sections of the
release notes (Breaking changes, Features, Security, Fixes, Documentation,
Maintenance; empty ones left out), each entry with its issue and pull
request numbers (see "Releasing" in the docs).

## 0.1.0

First release train: the experimental plugins `generic.routing.random` and
`tes.routing.distance`, resolved together and each tested against FOCA
0.18 and 0.19 (`foca>=0.18.0,<0.20`). Pins:

- `foca-plugin-generic-routing-random` 0.1.1
- `foca-plugin-tes-routing-distance` 0.1.1

### Features

- Add the meta distribution, with extras per group (`generic`, `tes`,
  `wes`), `all` and `experimental` (#1, #2).
- Pin the first plugins, with the subgroup extras `generic-routing` and
  `tes-routing` (#19, #20), in their versions for FOCA 0.18 and 0.19
  (#32, #36).
