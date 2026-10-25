# TES plugins

Plugins for gateways of the [GA4GH Task Execution Service (TES)
API](https://github.com/ga4gh/task-execution-schemas), such as proTES
(`apis = frozenset({"tes"})`): they read or change TES tasks, e.g., route by
the locations of a task's inputs, decrypt Crypt4GH inputs in an extra
executor, or allow only executor images of listed registries.

Subgroups, with their plugins as `<subgroup>/<name>/`: none yet. Planned
are, e.g., `routing/distance` (by the distance of inputs to backends) and
`policy/allowed-images`.

See [`plugins/README.md`](../README.md) for the groups and subgroups.
