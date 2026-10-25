# Releasing

!!! warning "The release workflow is an untested draft"

    `.github/workflows/release.yml` has not run yet. Before the first
    release, dry-run it end to end in a fork, on a test tag, publishing to
    TestPyPI (see
    [`MAINTAINERS.md`](https://github.com/elixir-cloud-aai/foca-plugins/blob/dev/MAINTAINERS.md)).
    Tags, published PyPI files and GitHub releases can't be changed
    afterwards.

Every package is released on its own, from `main`, by pushing its tag; the
release workflow does the rest. There are no manual steps after the tag:
**Re-run failed jobs** completes a partial release, with the distributions
built by the first attempt. Never use **Re-run all jobs**: it builds again,
and the sdists aren't reproducible (the wheels are), so PyPI and the GitHub
release could get different files.

## Versions

- **Plugins:** [Semantic Versioning](https://semver.org/); the version is
  static, in the plugin's `pyproject.toml` (`project.version`). Breaking
  changes of settings, parameters or behaviour bump the major version (the
  minor one before 1.0.0). Releases before 1.0.0 are GitHub pre-releases.
- **Meta distribution `foca-plugins`:** calendar versions, `YYYY.MM.N` (`N`
  counts the releases of the month, from 0). Each release is a release
  train: its extras pin plugin versions exactly, and its `foca` requirement
  declares the FOCA versions that the train was tested with.

## Changelogs and release notes

Each package has a `CHANGELOG.md`. Changes go under `## Unreleased` in the
pull request that makes them. For a release, that section becomes
`## <version>` with:

1. an intro paragraph saying what the release brings (with a link to
   upgrade notes, if any);
2. the grouped sections, empty ones left out, in this order:
   `### Breaking changes`, `### Features`, `### Security`, `### Fixes`,
   `### Documentation`, `### Maintenance`; each entry ends with its issue and
   pull request numbers.

The release workflow publishes this section as the GitHub release notes
(`python -m scripts.release notes <tag>`), followed by `## Commits`:
GitHub's generated notes since the package's previous tag (they credit the
authors), ending with their `**Full Changelog**` line. The previous tag is
the newest tag of the same distribution (`<distribution>-v<version>`, not
those of other packages) among the ancestors of the tagged commit,
pre-releases included. On a package's
first release, GitHub's notes would list the whole repository's history, so
`## Commits` lists the commits that changed the package's directory instead,
ending with a link to its history.

## Steps

1. Open a pull request against `dev` that sets the version in the
   package's `pyproject.toml` and turns `## Unreleased` of its changelog into
   `## <version>`. For a plugin that is part of the next release train, also
   update the pins of the meta distribution's extras;
   `python -m scripts.checks check` fails for pins that don't match.
2. Merge it, then move `main` forward to that commit (a fast-forward, as the
   rulesets allow).
3. Push the tag `<distribution>-v<version>` on that commit, e.g.,
   `foca-plugin-tes-routing-distance-v1.2.0` or `foca-plugins-v2026.11.0`.
   Push at most 3 tags per `git push`: GitHub creates no workflow events
   for pushes of more tags at once. Each tag gets a run of its own (the
   workflow's concurrency group is per tag).

## What the workflow does

`release.yml` runs for tags `foca-plugin*-v*`:

1. **Build distributions:** first, before anything is installed or built,
   checks that the tag names a package and its version on that commit and
   that the package may be published (the example plugin
   `generic.example.noop` never is); then that the commit is on `main`, the
   conventions hold and the changelog has the version's notes. It builds
   the wheel and sdist with pinned tools (uv by `astral-sh/setup-uv`, pinned
   by commit, which verifies uv's checksum; the build backend pinned with
   `build-constraint-dependencies`) and checks them (only the package's own
   module, its license, and `twine check` with twine from `uv.lock`).
2. **Attest distributions:** GitHub artifact attestations (SLSA build
   provenance).
3. **Publish to PyPI:** trusted publishing (OIDC, no API token) from the
   protected `pypi` environment, with PEP 740 attestations; already
   uploaded files are skipped, so re-runs of failed jobs complete an upload.
4. **Publish release:** the GitHub release of the tag, with the notes above
   and the distributions as assets, created as a draft and published last.

Each PyPI project trusts this workflow file (`release.yml`) in the
environment `pypi`: renaming either breaks publishing until the trusted
publishers are updated on PyPI.
