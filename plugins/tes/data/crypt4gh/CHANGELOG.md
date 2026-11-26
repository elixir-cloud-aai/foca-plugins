# Changelog of `foca-plugin-tes-data-crypt4gh`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `foca-plugin-tes-data-crypt4gh-v<version>`. Each version lists its
changes in the sections of the release notes (Breaking changes, Features,
Security, Fixes, Documentation, Maintenance; empty ones left out), each
entry with its issue and pull request numbers (see "Releasing" in the docs).

## 0.1.1

Support for FOCA 0.23: the plugin now installs alongside FOCA 0.19 to
0.23, with the decryption image
`elixircloud/foca-plugin-tes-data-crypt4gh-decrypt:0.1.1`; it changes
nothing else.

### Maintenance

- Support FOCA 0.23 (`foca>=0.19.0,<0.24`), tested against FOCA 0.23.0
  (#96, #97).

## 0.1.0

The first release of the plugin, the successor of proTES's Crypt4GH
middleware (experimental): it decrypts the Crypt4GH inputs of TES tasks in
an executor of its own, empties the volume of the decrypted files after the
task, and ships its decryption image, scanned, signed and attested, as
`elixircloud/foca-plugin-tes-data-crypt4gh-decrypt:0.1.0`.

### Features

- Add the plugin `tes.data.crypt4gh`, the successor of proTES's Crypt4GH
  middleware: decrypts the Crypt4GH inputs of TES tasks in an executor of
  its own, with the secret keys among the task's inputs, to a volume of its
  own; with the decryption image (`image/Dockerfile`), built and smoke
  tested in CI (#65, #66).
- Add a teardown executor after the task's own executors that empties the
  volume of the decrypted files (setting `teardown`, on by default; the
  decryption script's option `--clear`) (#67, #73).
- Add the setting `teardown_on_error` (off by default), which sets
  `ignore_error` on the task's own executors, so that the teardown runs
  after they fail where the backend supports it (#67, #73).
- Publish the decryption image with each release, on Docker Hub as
  `elixircloud/foca-plugin-tes-data-crypt4gh-decrypt:<version>`: scanned
  and smoke tested, then signed (Sigstore), attested and tagged by the
  release workflow; the image no longer contains pip, which the executor
  doesn't need (#69, #74).

### Maintenance

- Add an end-to-end test (`e2e/check.sh`, CI job `e2e`): proTES with the
  plugin sends tasks with Crypt4GH inputs, served over HTTP, to Funnel, a
  real TES backend; it checks the decrypted content, the teardown, a
  failing decryption and `teardown_on_error` (#68, #76).
