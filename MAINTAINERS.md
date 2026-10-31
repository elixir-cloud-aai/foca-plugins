# Maintainers

Settings of the repository and the services around it, aligned with
[cloud-registry](https://github.com/elixir-cloud-aai/cloud-registry), the
template of all FOCA repositories, and the setup tasks that are still open.

## Done

- **Repository** `elixir-cloud-aai/foca-plugins`: public, Apache-2.0, with
  the branches `dev` (default; all pull requests) and `main` (releases
  only), and the labels of cloud-registry.
- **Settings, as in cloud-registry:** squash merge only, branches deleted on
  merge, no wiki or discussions, topics; security policies (private
  vulnerability reporting); rulesets for `dev` and `main`, without required
  checks so far.
- **PyPI:** a pending trusted publisher for the meta distribution
  `foca-plugins`: workflow `release.yml`, environment `release`. The GitHub
  environment `release` is configured like FOCA's. The release workflow
  publishes from it.
- **Codecov:** the repository secret `CODECOV_TOKEN`; CI uploads one report
  per plugin, flagged with the distribution name (`codecov.yml`: 100%
  targets, carried forward flags).
- **Read the Docs:** the project `foca-plugins`; the tag automation is
  configured but not active, and pull request builds are off. Its build
  statuses come from the Read the Docs GitHub App, not from a user token.

## Open

1. **Required check `CI passed`:** add it to the rulesets of `dev` and `main`
   (source: GitHub Actions) once the first pull request (the scaffold) has
   reported it. It is the only required check of CI; job names may change
   without touching the rulesets.
2. **Read the Docs:** once there are real docs pages, turn on pull request
   builds and the tag automation, then add the Read the Docs check as a
   required check. Decide which tags build versions of the docs (e.g., only
   the meta distribution's `foca-plugins-v*`, as plugin tags are many and
   the docs cover the whole repository).
3. **PyPI trusted publishers for the plugins:** each plugin distribution
   needs a pending trusted publisher of its own (workflow `release.yml`,
   environment `release`) before its first release. None exist yet; the
   example `foca-plugin-generic-example-noop` is never published (the
   release workflow refuses its tags, and PyPI refuses its `Private :: Do
   Not Upload` classifier). Planned distributions (architecture plan,
   section 11.1):
    - `foca-plugin-generic-routing-random`
    - `foca-plugin-generic-identity-owner-tags`
    - `foca-plugin-tes-routing-distance`
    - `foca-plugin-tes-policy-allowed-images`
    - `foca-plugin-tes-data-crypt4gh` (deferred)
4. **First release:** the release workflow passed a dry run end to end in
   a fork (`uniqueg/foca-plugins`, tags `*-v0.1.0.dev1` on TestPyPI). Before
   the first real tag, check the `release` environment's required reviewers
   and tag rule (it must allow `foca-plugin*-v*`), and the tag ruleset
   (item 7).
5. **New FOCA minor versions:** the plugins currently declare
   `foca>=0.18.0,<0.19` (each in its own `pyproject.toml`). When FOCA
   releases 0.19.0 (Dependabot proposes it), test the plugins against it,
   raise their upper bounds, and raise the lowest FOCA of the test matrix
   (`ci.yml`; move `coverage: true` to the new lowest entry) as needed;
   then release the plugins whose range changed.
6. **Pinned tools that Dependabot doesn't bump:** the uv version
   (`UV_VERSION` in `ci.yml` and `release.yml`; Dependabot bumps the
   `astral-sh/setup-uv` action itself), the build backend
   (`build-constraint-dependencies` in the root `pyproject.toml`) and
   cffconvert (`ci.yml`); update them by hand from time to time. twine is
   locked in `uv.lock` (dependency group `release`).
7. **Tag ruleset (maintainer action):** add a tag ruleset that protects
   `foca-plugin*-v*` (which covers the meta distribution's `foca-plugins-v*`)
   from updates and deletion, with no bypass for regular contributors, before
   the first release. The release workflow builds the tagged commit and
   fails if the tag moves during the run, but only the ruleset stops a moved
   tag from pointing at other code than its published files.

## Conventions kept from cloud-registry

The CI naming (`ci.yml` / `CI`, the gate `passed` / `CI passed`, job names),
the pre-commit hooks and rules, Dependabot against `dev`, the docs set-up,
the license, the code of conduct and the security policy follow
cloud-registry. cloud-registry has no issue or pull request templates and no
release-notes configuration, so neither has this repository.
