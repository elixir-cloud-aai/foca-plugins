# Changelog of `foca-plugin-tes-data-crypt4gh`

The plugin follows [Semantic Versioning](https://semver.org/); releases are
tagged `foca-plugin-tes-data-crypt4gh-v<version>`. Each version lists its
changes in the sections of the release notes (Breaking changes, Features,
Security, Fixes, Documentation, Maintenance; empty ones left out), each
entry with its issue and pull request numbers (see "Releasing" in the docs).

## Unreleased

### Features

- Add the plugin `tes.data.crypt4gh`, the successor of proTES's Crypt4GH
  middleware: decrypts the Crypt4GH inputs of TES tasks in an executor of
  its own, with the secret keys among the task's inputs, to a volume of its
  own; with the decryption image (`image/Dockerfile`), built and smoke
  tested in CI, not yet published (#65, #66).
- Add a teardown executor after the task's own executors that removes the
  decrypted files (`teardown`, on by default; the script's `--remove`
  option), and the opt-in setting `teardown_on_error`, which sets
  `ignore_error` on the task's own executors so that the teardown runs
  after they fail where the backend supports it (#67, #73).
