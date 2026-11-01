# Releasing

!!! warning "Releases can't be changed"

    Tags, published PyPI files and GitHub releases can't be changed
    afterwards. Dry-run changes of the release workflow end to end in a
    fork, on a test tag, publishing to TestPyPI, before the next release.

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
- **All packages:** versions are normalized [PEP 440](https://peps.python.org/pep-0440/)
  versions without epoch or local part, e.g., `1.2.0`, `1.2.0rc1` or
  `1.2.0.dev0` (not `1.2.0-rc1` or `1.2.0RC1`), so that the version, the
  names of the built files and the tag agree; `python -m scripts.checks
  check` refuses others.
- **Meta distribution `foca-plugins`:** [Semantic
  Versioning](https://semver.org/) as well, like the shared package version
  of [cloud-components](https://github.com/elixir-cloud-aai/cloud-components).
  Each release is a release train: its extras pin plugin versions exactly,
  and its `foca` requirement declares the FOCA versions that the train was
  tested with. Its version follows from the changes of the pins since the
  previous train:
    - **major:** a pinned plugin gets a major version bump, or a plugin is
      removed from the train;
    - **minor:** a plugin is added, or a pinned plugin gets a minor version
      bump;
    - **patch:** pinned plugins get only patch version bumps.

    Changes of the metadata count the same way: dropping FOCA versions from
    the `foca` requirement counts as major, adding FOCA versions as minor,
    and moving a plugin between `[experimental]` and `[all]` (a change of
    its maturity) as minor. The highest of these bumps sets the version.

    While the pinned plugins are 0.x, the meta distribution stays 0.x too:
    what would bump its major version (a major bump of a pin, or a removed
    plugin) bumps its minor version instead; e.g., 0.1.0 becomes 0.2.0 when
    a pin goes from 0.1.x to 0.2.0. The release pull request sets the
    version by these rules; no check enforces them. Its releases before
    1.0.0 are GitHub pre-releases, too.

## Changelogs and release notes

Each package has a `CHANGELOG.md`. Changes go under `## Unreleased` in the
pull request that makes them. For a release, that section becomes
`## <version>` (once: the release fails if the changelog has two sections
of the version) with:

1. an intro paragraph saying what the release brings (with a link to
   upgrade notes, if any);
2. the grouped sections, empty ones left out, in this order:
   `### Breaking changes`, `### Features`, `### Security`, `### Fixes`,
   `### Documentation`, `### Maintenance`; each entry ends with its issue and
   pull request numbers.

The release workflow publishes this section as the GitHub release notes, in
the format of all release notes of the organization:

1. the intro paragraph;
2. the grouped sections one level up, as `## Breaking changes` to
   `## Maintenance` (the changelog nests them under `## <version>`; deeper
   headings move up as well, code blocks stay as they are);
3. `## Commits`: GitHub's generated notes since the package's previous tag,
   copied verbatim without their `## What's Changed` header (they credit the
   authors, and list new contributors under `## New Contributors`);
4. last, GitHub's `**Full Changelog**: …` line, which ends the generated
   notes.

`python -m scripts.release notes <tag>` prints items 1 and 2, and refuses a
section without an intro paragraph, with unknown sections or with sections
in another order, with headings of level 1 or 2 (also setext headings: a
line of only `=` or `-` right below text), with deeper headings before the
first grouped section, or with an unclosed code block (HTML comments aren't
skipped: keep headings out of them). `python -m scripts.checks check`
(pre-commit and CI) checks the section of each package's current version the same way, so that
the release pull request fails rather than the tag. The previous tag is the
newest tag of the same distribution (`<distribution>-v<version>`, not those
of other packages) among the ancestors of the tagged commit, pre-releases
included (`python -m scripts.release previous`).

GitHub's generated notes ignore paths: a plugin's `## Commits` lists every
pull request of the repository since the plugin's previous tag, also those
of the docs, the meta distribution and other plugins. This is accepted, as
the format copies GitHub's notes verbatim; the changelog section above them
says what changed in the package.

On a package's first release there is no previous tag, and without one
GitHub doesn't start at the beginning of the history, but at the newest
release of any package of the repository (e.g., a plugin released on the
same commit, which leaves the list empty). So `## Commits` of a first
release lists the commits that changed the package's directory instead
(`git log`), ending with a link to its history in place of GitHub's
`**Full Changelog**` line.

## Steps

1. Open a pull request against `dev` that sets the version in the
   package's `pyproject.toml` and turns `## Unreleased` of its changelog into
   `## <version>`. For a plugin, also pin the new version in the meta
   distribution's extras: every released plugin is in its group's and
   subgroup's extras, and in `[all]` if its `maturity` is `stable` or
   `deprecated`, or in `[experimental]` if it is `experimental`;
   `python -m scripts.checks check` fails otherwise, and for pins of
   versions without a changelog section. A pinned plugin also needs the
   entry `<distribution> = { workspace = true }` in `[tool.uv.sources]` of
   `meta/pyproject.toml` (added with its first pin, if it isn't there yet;
   the check fails without it), as uv refuses workspace members as
   dependencies without one. The built distributions keep the exact pins.
   Then re-lock (`uv lock`) and commit `uv.lock`: CI installs with
   `uv sync --locked` and fails on an outdated lock file.
2. Merge it, then move `main` forward to that commit (a fast-forward, as the
   rulesets allow).
3. Push the tag `<distribution>-v<version>` on that commit, e.g.,
   `foca-plugin-tes-routing-distance-v1.2.0` or `foca-plugins-v0.2.0`.
   Push at most 3 tags per `git push`: GitHub creates no workflow events
   for pushes of more tags at once. Each tag gets a run of its own (the
   workflow's concurrency group is per tag), so nothing orders the runs of
   tags pushed together.
4. When a release train pins new plugin versions, push the plugin tags
   first, and the meta distribution's tag only once all its pinned plugin
   versions are on PyPI (their runs are green): a train released before its
   pins can't be installed. The meta distribution's build job checks this
   (`python -m scripts.release pins <tag>`) and fails before anything is
   built if a pinned version isn't on PyPI yet; then **Re-run failed jobs**
   once the plugins are released.

The first release of a distribution needs a pending trusted publisher on
PyPI (owner `elixir-cloud-aai`, repository `foca-plugins`, workflow
`release.yml`, environment `release`). PyPI allows a pending publisher with
these fields for only one project at a time, so first releases happen one
after the other: add the pending publisher for one distribution, push its
tag and let the release finish, then add it for the next one. Once a
project exists, the same publisher can serve any number of projects.

Each PyPI project trusts this workflow file (`release.yml`) in the
environment `release`. When the environment or the workflow file is renamed,
update the trusted publisher of every existing project on PyPI (Manage
project > Publishing), and the pending publisher of a distribution not
released yet, before the next tag of each: until then, its **Publish to
PyPI** job fails (the tag then exists already; fix the publisher and
**Re-run failed jobs**). The rename of the environment from `pypi` to
`release` (2026-10, #25) was done this way for
`foca-plugin-generic-routing-random`, `foca-plugin-tes-routing-distance` and
the pending publisher of `foca-plugins`.

## New FOCA versions

Each plugin declares the FOCA versions that it supports (`foca>=X,<Y` in
its `pyproject.toml`), and so does the meta distribution. When FOCA
releases a new minor version (Dependabot proposes it for `uv.lock`):

1. Add it to the test matrix (`ci.yml`, job `test`) as the newest
   release: to the list `foca`, and as an `include` entry with its
   `foca-spec` (e.g., `foca==0.20.0`), without which the job installs no
   FOCA. Fix what fails.
2. Raise the upper bounds of the plugins that pass, of the template
   `generic.example.noop` (new plugins start from it) and of the meta
   distribution.
3. Old FOCA versions are dropped for all plugins at once, as the matrix
   tests every plugin with every FOCA version: raise the lower bounds of
   the plugins and of the meta distribution, remove the old versions from
   the matrix and move `coverage: true` to the new lowest entry.
4. Release the plugins whose range changed (a patch release if the range
   only widened; dropped FOCA versions are a breaking change), then a
   release train that pins them, versioned as in [Versions](#versions)
   (adding FOCA versions counts as minor, dropping them as major).

## What the workflow does

`release.yml` runs for tags `foca-plugin*-v*`, on the pushed commit
(`github.sha`), not on whatever the tag points to later; a tag ruleset
should keep the tags from moving (one that protects `foca-plugin*-v*`, which
covers the meta distribution's `foca-plugins-v*`, from updates and
deletion; the workflow fails if a tag moves during the run, but only the
ruleset stops a tag from pointing at other code than its published
files):

1. **Build distributions:** first, before anything is installed or built,
   checks that the tag names a package and its version on that commit and
   that the package may be published (the example plugin
   `generic.example.noop` never is); then that the commit is on `main`, the
   conventions hold, the changelog has the version's notes in the format
   above and, for the meta distribution, that its pinned plugin versions are
   on PyPI. It builds
   the wheel and sdist with pinned tools (uv by `astral-sh/setup-uv`, pinned
   by commit, which verifies uv's checksum; the build backend pinned with
   `build-constraint-dependencies`) and checks them (only the package's own
   module, its license, its `py.typed`, and `twine check` with twine from
   `uv.lock`).
2. **Attest distributions:** GitHub artifact attestations (SLSA build
   provenance).
3. **Publish to PyPI:** fails if the tag no longer points to the built
   commit; then trusted publishing (OIDC, no API token) from the
   protected `release` environment, with PEP 740 attestations; already
   uploaded files are skipped, so re-runs of failed jobs complete an upload.
4. **Publish release:** checks the tag again; the GitHub release of the
   tag, with the notes above and the distributions as assets, created as a
   draft and published last. A release published already must have exactly
   the built distributions (by SHA-256), or the job fails.

Renaming the workflow file or the environment `release` breaks publishing
until the trusted publishers are updated on PyPI (see [Steps](#steps)).
