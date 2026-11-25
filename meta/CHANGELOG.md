# Changelog of `foca-plugins`

Each release of the meta distribution is a release train, tagged
`foca-plugins-v<version>` with a semantic version, set by the changes of
its pins. Each lists the plugin versions that its extras pin and the FOCA
versions that it was tested with, then the changes in the sections of the
release notes (Breaking changes, Features, Security, Fixes, Documentation,
Maintenance; empty ones left out), each entry with its issue and pull
request numbers (see "Releasing" in the docs).

## 0.4.0

Release train for FOCA 0.23: the plugins of 0.3.0 in new versions, plus
`tes.data.crypt4gh`, each tested against FOCA 0.19, 0.20, 0.21, 0.22 and
0.23 (`foca>=0.19.0,<0.24`; FOCA 0.18 is no longer supported). Pins:

- `foca-plugin-generic-routing-random` 0.2.0
- `foca-plugin-tes-data-crypt4gh` 0.1.1
- `foca-plugin-tes-routing-distance` 0.2.0

### Breaking changes

- Drop FOCA 0.18 (`foca>=0.19.0`), as all plugins do; `tes.routing.distance`
  0.2.0 has breaking changes of its own (see its changelog) (#21).

### Features

- Pin `tes.data.crypt4gh` 0.1.1 in the extras `tes`, `tes-data` and
  `experimental` (#96, #97).

### Maintenance

- Support FOCA 0.23 in the meta distribution (`foca>=0.19.0,<0.24`),
  tested against FOCA 0.23.0 (#96, #97).
- Pin the plugins' versions for FOCA 0.23 (#96, #97).

## 0.3.0

Release train for FOCA 0.22: the same plugins as in 0.2.0, in new versions,
each tested against FOCA 0.18, 0.19, 0.20, 0.21 and 0.22
(`foca>=0.18.0,<0.23`). Pins:

- `foca-plugin-generic-routing-random` 0.1.3
- `foca-plugin-tes-routing-distance` 0.1.3

### Maintenance

- Support FOCA 0.22 in the meta distribution (`foca>=0.18.0,<0.23`), tested
  against FOCA 0.22.0 (#61, #62).
- Support FOCA 0.21 in the meta distribution (`foca>=0.18.0,<0.22`), tested
  against FOCA 0.21.0 (#55, #56).
- Pin the plugins' versions for FOCA 0.21 and 0.22 (#63, #64).

## 0.2.0

Release train for FOCA 0.20: the same plugins as in 0.1.0, in new versions,
each tested against FOCA 0.18, 0.19 and 0.20 (`foca>=0.18.0,<0.21`). Pins:

- `foca-plugin-generic-routing-random` 0.1.2
- `foca-plugin-tes-routing-distance` 0.1.2

### Documentation

- Install plugins in derived images with uv, as images on FOCA 0.20 have
  no pip (#53, #54).

### Maintenance

- Support FOCA 0.20, and pin the plugins' versions for it (#53, #54).

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
