[![ELIXIR On-Cloud](images/logo_elixir_on_cloud.png)](https://elixir-on.cloud/)

# FOCA plugins

--8<-- "README.md:synopsis"

--8<-- "README.md:status"

## How it fits together

- **[FOCA](https://foca.readthedocs.io/)** provides the plugin engine
  (`foca.plugins`): the plugin API (`Plugin`, the hooks of the phases
  `request`, `route`, `forward` and `response`, the plain request models),
  the loader of entry points, the pipelines, and the provenance of each
  request. Its [plugins page](https://foca.readthedocs.io/en/latest/plugins/)
  documents the API and the configuration (the `plugins` section).
- **Gateway apps** such as [proTES](https://github.com/elixir-cloud-aai/proTES)
  (and, later, [proWES](https://github.com/elixir-cloud-aai/proWES)) declare
  themselves as gateways to FOCA, run the pipelines in their controllers,
  and forward requests to their backends.
- **This repository** holds plugins: installable distributions that
  register a plugin class under the entry point group `foca.plugins`.
  Operators install them into the app's image and enable them in the app's
  configuration.

## Layout

--8<-- "README.md:layout"

## Contents

- [Installing](installing.md): extras of the meta distribution, single
  distributions, and an image derived from the app's image.
- [Writing a plugin](writing-plugins.md): the conventions of this
  repository and the template.
- [Plugin catalogue](catalogue.md): the plugins of this repository.
- [Contributing](contributing/index.md): development setup, checks, pull requests,
  commit messages and the Python guidelines.
- [Releasing](releasing.md): versions, tags, changelogs and the release
  workflow.

## Citation

--8<-- "README.md:citation"

## License

This project is covered by the [Apache License
2.0](https://www.apache.org/licenses/LICENSE-2.0) also [shipped with this
repository](https://github.com/elixir-cloud-aai/foca-plugins/blob/dev/LICENSE)
and with each distribution.

## Contact

The project is a collaborative effort under the umbrella of [ELIXIR
On-Cloud](https://elixir-on.cloud/). Follow the link to get in touch with us
via chat or email. Please mention the name of this project for any inquiry,
proposal, question etc.
