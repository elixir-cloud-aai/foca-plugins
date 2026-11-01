# Changelog of `foca-plugin-generic-routing-random`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `foca-plugin-generic-routing-random-v<version>`. Each version lists
its changes in the sections of the release notes (Breaking changes,
Features, Security, Fixes, Documentation, Maintenance; empty ones left
out), each entry with its issue and pull request numbers (see "Releasing"
in the docs).

## 0.1.1

Support for FOCA 0.19: the plugin now installs alongside FOCA 0.18 and
0.19.

### Maintenance

- Support FOCA 0.19 (`foca>=0.18.0,<0.20`), tested against FOCA 0.18.0,
  0.19.0 and `dev` (#32, #36).

## 0.1.0

First release of the plugin: routing to the candidate backends in random
order.

### Features

- Add the plugin `generic.routing.random`: routes to the candidate backends
  in random order, optionally seeded for tests (#7, #12).
