# Generic plugins

Plugins for any gateway API (`apis = frozenset({"*"})`): they use only what
all gateways share, i.e., the caller (`ctx.principal`), headers, the
candidate backends and their labels, but not the payload's schema (e.g., no
TES task fields). Examples: random or ordered routing, the caller's identity
as backend tags.

Subgroups, with their plugins as `<subgroup>/<name>/`:

- `example/`: [`noop`](example/noop/), the template of new plugins (never
  published).
- `routing/`: [`random`](routing/random/), routing in random order.

See [`plugins/README.md`](../README.md) for the groups and subgroups.
