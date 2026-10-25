# Contributing

Thanks for your interest in the FOCA plugins! Bug reports, feature
requests, new plugins, discussions, documentation and code are all welcome.
The project follows the [contributor guide of ELIXIR
On-Cloud](https://elixir-cloud-aai.github.io/contribute/); in short:

- Follow our [code of conduct](CODE_OF_CONDUCT.md).
- Report vulnerabilities privately, as described in the [security
  policy](SECURITY.md).
- Work on [issues](https://github.com/elixir-cloud-aai/foca-plugins/issues):
  open or find one and agree on the approach before you change code; for a
  new plugin, describe what it does, its phases and its data transfers.
- Branch off `dev` and open a pull request against `dev` (not `main`, which
  only holds releases) that closes the issue; it is squash-merged once the
  checks pass and it has been reviewed.
- Title pull requests as [Conventional
  Commits](https://www.conventionalcommits.org/)
  (`<type>[(<scope>)][!]: <description>`, at most 50 characters), with the
  plugin's path as the scope for changes of one plugin, e.g.,
  `feat(tes/routing/distance): add ...`.
- Keep the checks green and the coverage at 100%, with docstrings, type hints
  and the pre-commit hooks; update the plugin's README and changelog.

Maintainers find the repository's settings and the open setup tasks in
[`MAINTAINERS.md`](MAINTAINERS.md).

**See [Contributing in the
documentation](https://foca-plugins.readthedocs.io/en/latest/contributing/)
for the details**, including the development setup and the Python
guidelines, and [Writing a
plugin](https://foca-plugins.readthedocs.io/en/latest/writing-plugins/).
