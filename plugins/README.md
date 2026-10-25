# Plugins

One directory per plugin: `<group>/<subgroup>/<name>/`, each a distribution
of its own (`foca-plugin-<group>-<subgroup>-<name>`) with the plugin ID
`<group>.<subgroup>.<name>` and the import path
`foca_plugins.<group>.<subgroup>.<name>`.

**Groups** follow the API of the gateway that a plugin works with:

| Group | Plugins for | Gateway apps |
| --- | --- | --- |
| [`generic`](generic/) | any API: they use only what all gateways share (the caller, headers, backends) | all |
| [`tes`](tes/) | the [GA4GH TES API](https://github.com/ga4gh/task-execution-schemas): they read or change TES tasks | proTES |
| [`wes`](wes/) | the [GA4GH WES API](https://github.com/ga4gh/workflow-execution-service-schemas): they read or change WES runs | proWES (once it adopts FOCA's plugin engine) |

**Subgroups** name the function, the same in every group, e.g.:

| Subgroup | Plugins that | Phases |
| --- | --- | --- |
| `routing` | choose and order the backends | `route` |
| `policy` | reject requests that a policy doesn't allow | `request` |
| `data` | change the data of requests, e.g., add executors or inputs | `request`, `forward` |
| `identity` | pass on or record the caller's identity, e.g., as tags | `request`, `forward` |
| `example` | show the conventions; never published | any |

Add a subgroup with its first plugin; reuse an existing one where it fits.
See "Writing a plugin" in the docs, and the
[catalogue](https://foca-plugins.readthedocs.io/en/latest/catalogue/) for
the plugins that exist.
