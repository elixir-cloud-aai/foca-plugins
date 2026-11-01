# Contributing

This project is a community effort and lives off your contributions, be it
bug reports, feature requests, new plugins, discussions, documentation or
code. It follows the [contributing guide of ELIXIR
On-Cloud](https://github.com/elixir-cloud-aai/.github/blob/main/CONTRIBUTING.md),
which covers getting started, issues, branches and pull requests, commit
messages and pull request titles, the code of conduct and the security
policy. This page covers what is specific to this repository; for a new
plugin, see [Writing a plugin](../writing-plugins.md).

## Development

Development needs [uv](https://docs.astral.sh/uv/) (which installs a
suitable Python, 3.11 or later, if needed) and Git. The repository root is a
[uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/): one
environment (`.venv/`) with every package installed in editable mode, plus
the development tools (dependency group `dev`).

```bash
git clone https://github.com/elixir-cloud-aai/foca-plugins.git
cd foca-plugins
uv sync  # all packages and the dev tools, as locked in `uv.lock`
uv run pre-commit install  # run the checks on every commit
uv run pre-commit run --all-files  # ruff, mypy, semgrep, ast-grep, conventions
uv run pre-commit run --all-files --hook-stage manual gitleaks-all  # secrets
uv run pytest  # all tests: the plugins', the scripts' and the docs hooks'
```

Run `pre-commit` in the environment (`uv run`): the mypy, ast-grep and
conventions hooks use the installed packages. The first run sets up the
other hooks, which takes a few minutes.

CI tests each plugin on its own, in an environment with only that plugin and
the dev tools (`uv sync --package <distribution> --group dev`), so that a
plugin can't rely on another's dependencies; the commands of one plugin's
checks are in [Writing a plugin](../writing-plugins.md#tests-and-checks). Its
change detection (`python -m scripts.packages changed <base> <head>`) runs
the tests of the plugins that a pull request affects: changes of a plugin's
directory affect that plugin; those of the meta distribution and of shared
files (the root `pyproject.toml`, `uv.lock`, workflows, scripts, rules)
affect all plugins; those of the docs affect none. The gate `CI passed` is
the only required check.

When you change dependencies, run `uv lock` (or `uv sync`) and commit
`uv.lock`; CI fails if it is not up to date. Project-specific rules are in
`.semgrep/` (security) and `.ast-grep/` (e.g., no framework imports in
plugins); suppress a false positive with a comment with the reason on its
line: `# noqa: <code>` (ruff), `# type: ignore[<code>]` (mypy),
`# nosemgrep: <rule-id>`, `# ast-grep-ignore: <rule-id>` or
`# gitleaks:allow`.

Dependabot updates the dependencies, the GitHub Actions and the pre-commit
hooks. Update these pinned tools by hand from time to time, as it doesn't:

- the uv version (`UV_VERSION` in `ci.yml` and `release.yml`; Dependabot
  bumps only the action `astral-sh/setup-uv`);
- the build backend (`build-constraint-dependencies` in the root
  `pyproject.toml`);
- cffconvert (`ci.yml`, job `lint`).

twine, the release workflow's other tool, is locked in `uv.lock`
(dependency group `release`), so Dependabot updates it.

### Documentation

This documentation is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/), from
`docs/` and `mkdocs.yml`, by Read the Docs (`.readthedocs.yaml`) and in CI;
warnings, e.g., of broken links, fail the build. The home page includes
sections of `README.md`, and the [plugin catalogue](../catalogue.md) is
generated from the plugins' READMEs (`docs/hooks.py`).

```bash
uv sync --group docs
uv run mkdocs build --strict  # into `site/`, ignored by Git
uv run mkdocs serve
```

## Commit messages and pull request titles

- The scope of a change of one plugin is its path below `plugins/`, e.g.,
  `feat(tes/routing/distance): ...`; that of the meta distribution is
  `meta`.

Use `feat`, `fix`, `perf`, `refactor` and `style` only for changes of what
is published: the plugins (`plugins/`) and the meta distribution (`meta/`).

## Citation metadata

The repository is cited with the metadata in
[`CITATION.cff`](https://github.com/elixir-cloud-aai/foca-plugins/blob/dev/CITATION.cff)
([Citation File Format](https://citation-file-format.github.io/)), which
GitHub ("Cite this repository") uses. It has no version, as each
distribution has its own. Its authors follow the [citation metadata rule of
the contributing
guide](https://github.com/elixir-cloud-aai/.github/blob/main/CONTRIBUTING.md#citation-metadata).
CI validates the file (`cffconvert --validate`).

## Python guidelines

Plugins support the Python versions that FOCA supports (3.11 and later;
`requires-python`), which CI tests (3.11, 3.13 and 3.15). Most of the
following is checked by pre-commit and CI; the rest is checked in reviews.

- **Packaging:** each package's metadata and dependencies are in its
  `pyproject.toml`; the tool configuration and the development dependencies
  (dependency groups `dev` and `docs`) are in the root `pyproject.toml`.
  Don't add `setup.py` or requirements files.
- **Formatting and linting:** [ruff](https://docs.astral.sh/ruff/) formats
  the code (`ruff format`, compatible with Black, 88 characters per line) and
  lints it (`ruff check`) with the rule set shared by FOCA and FOCA apps:
  pycodestyle, Pyflakes, isort, flake8-bugbear, flake8-comprehensions,
  flake8-simplify, Pylint, Ruff's own rules, pyupgrade, bandit (security),
  logging format and pydocstyle, also for the tests (root `pyproject.toml`,
  where each ignored rule says why). Prefer explicit checks to suppressions.
- **Docstrings:** Google-style docstrings at all levels: packages, modules,
  classes, methods and functions, also private ones and tests, with a
  one-line summary and, where applicable, the sections `Args`, `Returns` (or
  `Yields`), `Raises` and, for classes, `Attributes`. The first line of a
  plugin class's docstring is its title for clients, the rest its
  description.
- **Type hints:** complete type hints for all functions and methods (also of
  the tests), and generic types with their parameters (e.g.,
  `dict[str, Any]`). [mypy](https://mypy.readthedocs.io/) checks them
  (`disallow_untyped_defs`, `disallow_any_generics`), through
  `python -m scripts.packages mypy`, which puts the plugins' `src/`
  directories on the search path.
- **Tests:** [pytest](https://docs.pytest.org/), with coverage measured by
  [coverage](https://coverage.readthedocs.io/) (branches included): keep it
  at 100% of each plugin's `src/` and of `scripts/` and `docs/hooks.py`,
  which is enforced (`fail_under` in the root `pyproject.toml`, and
  Codecov's checks, `codecov.yml`). Add tests that fail without your change.
- **Further checks:** [ast-grep](https://ast-grep.github.io/) (rules in
  `.ast-grep/`, with tests), [semgrep](https://semgrep.dev/) (security rules
  in `.semgrep/`), [gitleaks](https://github.com/gitleaks/gitleaks)
  (secrets), [actionlint](https://github.com/rhysd/actionlint) (workflows),
  [pip-audit](https://github.com/pypa/pip-audit) (known vulnerabilities of
  each plugin's dependencies, in CI), and the package conventions
  (`python -m scripts.checks check`).
