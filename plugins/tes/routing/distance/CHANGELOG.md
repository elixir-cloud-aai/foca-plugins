# Changelog of `foca-plugin-tes-routing-distance`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `foca-plugin-tes-routing-distance-v<version>`. Each version lists its changes in the
sections of the release notes (Breaking changes, Features, Security, Fixes,
Documentation, Maintenance; empty ones left out), each entry with its issue
and pull request numbers (see "Releasing" in the docs).

## Unreleased

### Features

- Add the plugin `tes.routing.distance`: routes TES tasks to the backends closest to their inputs, with locations from the backend list, a local GeoIP database (default) or, as an opt-in, DB-IP and Nominatim; lookups cached per process (#8).
