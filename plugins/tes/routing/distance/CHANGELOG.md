# Changelog of `foca-plugin-tes-routing-distance`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `foca-plugin-tes-routing-distance-v<version>`. Each version lists
its changes in the sections of the release notes (Breaking changes,
Features, Security, Fixes, Documentation, Maintenance; empty ones left
out), each entry with its issue and pull request numbers (see "Releasing"
in the docs).

## Unreleased

### Breaking changes

- Require FOCA 0.19 (`foca>=0.19.0,<0.23`), for its plugin store (#21).
- With `provider: dbip`, each caller now gets at most 100 uncached
  lookups per day by default (`dbip_user_daily_lookups`; set `null` for
  the earlier behaviour), and the plugin keeps locations and lookup counts
  in FOCA's plugin store: with a database, in its collection
  `plugin_store`, as personal data (hashed keys) for up to `cache_ttl` or a
  day; set FOCA's `plugins.defaults.store.key_secret` and review
  `max_ttl` before upgrading (#21).

### Features

- With `provider: dbip`, keep the locations of addresses (also misses and
  failures) in FOCA's plugin store (`ctx.store`), shared by all processes
  and replicas, instead of per process (#21).
- With `provider: dbip`, limit the lookups of each caller to
  `dbip_user_daily_lookups` (default 100) per day, counted in the plugin
  store by a hash of the caller's identity; `null` turns the limit off
  (#21).
- Declare the plugin's entries in the plugin store (`data_stored`) (#21).

## 0.1.3

Support for FOCA 0.21 and 0.22: the plugin now installs alongside FOCA
0.18 to 0.22.

### Maintenance

- Support FOCA 0.22 (`foca>=0.18.0,<0.23`), tested against FOCA 0.22.0
  (#61, #62).
- Support FOCA 0.21 (`foca>=0.18.0,<0.22`), tested against FOCA 0.21.0
  (#55, #56).

## 0.1.2

Support for FOCA 0.20: the plugin now installs alongside FOCA 0.18, 0.19
and 0.20.

### Documentation

- Install the plugin in derived images with uv, as images on FOCA 0.20
  have no pip (#53, #54).

### Maintenance

- Support FOCA 0.20 (`foca>=0.18.0,<0.21`), tested against FOCA 0.18.0,
  0.19.0, 0.20.0 and `dev` (#53, #54).

## 0.1.1

Support for FOCA 0.19: the plugin now installs alongside FOCA 0.18 and
0.19. It still caches lookups per process, not in FOCA 0.19's plugin
store.

### Maintenance

- Support FOCA 0.19 (`foca>=0.18.0,<0.20`), tested against FOCA 0.18.0,
  0.19.0 and `dev` (#32, #36).

## 0.1.0

First release of the plugin: routing of TES tasks to the backends closest
to their inputs.

### Features

- Add the plugin `tes.routing.distance`: routes TES tasks to the backends
  closest to their inputs, with locations from the backend list, a local
  GeoIP database (default) or, as an opt-in, DB-IP and Nominatim; name
  resolution bounded in time and threads, lookups cached per process
  (#8, #13).
