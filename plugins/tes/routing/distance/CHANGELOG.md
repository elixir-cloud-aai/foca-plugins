# Changelog of `foca-plugin-tes-routing-distance`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `foca-plugin-tes-routing-distance-v<version>`. Each version lists
its changes in the sections of the release notes (Breaking changes,
Features, Security, Fixes, Documentation, Maintenance; empty ones left
out), each entry with its issue and pull request numbers (see "Releasing"
in the docs).

## 0.1.1

Support for FOCA 0.19: the plugin now installs alongside FOCA 0.18 and
0.19. It still caches lookups per process, not in FOCA 0.19's plugin
store.

### Maintenance

- Support FOCA 0.19 (`foca>=0.18.0,<0.20`), tested against FOCA 0.18.0,
  0.19.0 and `dev` (#32, #PRNUM).

## 0.1.0

First release of the plugin: routing of TES tasks to the backends closest
to their inputs.

### Features

- Add the plugin `tes.routing.distance`: routes TES tasks to the backends
  closest to their inputs, with locations from the backend list, a local
  GeoIP database (default) or, as an opt-in, DB-IP and Nominatim; name
  resolution bounded in time and threads, lookups cached per process
  (#8, #13).
