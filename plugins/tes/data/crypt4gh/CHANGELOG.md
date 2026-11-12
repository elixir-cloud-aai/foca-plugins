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
  tested in CI (#65, #66).
- Publish the decryption image with each release, on Docker Hub as
  `elixircloud/foca-plugin-tes-data-crypt4gh-decrypt:<version>`: scanned
  and smoke tested, then signed (Sigstore), attested and tagged by the
  release workflow; the image no longer contains pip, which the executor
  doesn't need (#69, #PR).
