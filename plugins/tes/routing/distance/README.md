# tes.routing.distance

<!-- --8<-- [start:summary] -->
Routes TES tasks to the backends closest to their input data: ranks the
candidate backends by their distance to the locations of the task's inputs.
Locations come from the backend list or from a local GeoIP database
(offline, the default); online lookups are an explicit opt-in. The
successor of proTES's distance-based task distribution.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `tes.routing.distance` |
| Distribution | `foca-plugin-tes-routing-distance` |
| Import path | `foca_plugins.tes.routing.distance` |
| APIs | `tes` |
| Phases | `route` |
| Capabilities | `routing-by-data-locality` |
| Maturity | experimental |
| Data transfers | none by default; with `provider: dbip`, db-ip.com and Nominatim (see [Data transfers](#data-transfers)) |
| FOCA | 0.18 (`>=0.18.0,<0.19`) |

## What it does

`route`:

1. Takes the `url`s of the task's `inputs`, as the client submitted them
   (`ctx.original`), and their hosts, for the schemes `http`, `https`,
   `ftp`, `ftps` and `sftp`. Other inputs are left out: those without URL
   (inline `content`), `file:///…`, and buckets (e.g., `s3://bucket/key`,
   `gs://…`), whose names say nothing about where the data is.
2. Locates each host, at most `max_hosts` per task: host names are
   resolved with the system resolver, all at once, within half of
   `resolve_timeout` (and never beyond the step's deadline), and the first
   **public** address is looked up by the provider. Private addresses
   (e.g., in-cluster storage) are never looked up.
3. Locates each candidate backend: its location in the backend list
   (`location: {latitude: …, longitude: …}`, FOCA's `Backend.location`)
   if configured, or else that of its host's public address (unless
   `locate_backends: false`); backend names are resolved within the rest
   of `resolve_timeout`.
4. Ranks the located backends by the sum of the great-circle distances to
   the located inputs (each input URL counts once, so hosts with more
   inputs weigh more; distances are computed once per host), closest
   first; ties keep the candidates' order.
   Backends without location follow, in their order. No candidate is
   dropped.

It raises `NotApplicable` (so that the step's next alternative routes, e.g.,
[`generic.routing.random`](../../../generic/routing/random/README.md) or
FOCA's `foca.routing.ordered`) if the task has no input URLs, if none of
its inputs could be located, or if none of the candidates could be
located. It never rejects a request.

Annotations, counts only (hosts, addresses and places of inputs could be
personal data): `inputs` (input URLs), `input_hosts`, `located_inputs`
(input URLs with a location), `located_backends`, `lookups` (by the
provider), `cache_hits`, `failed_lookups` and `skipped_lookups`
(addresses not looked up for lack of time or beyond `dbip_max_lookups`).
The order is in the provenance of each request (`route.candidates_out`).

### Cache and limits

Each process caches the lookups of addresses, also misses (addresses
without location), so that backends are looked up once per process until
the entry expires, not per task: `cache_ttl` (1 day) for locations,
`cache_miss_ttl` (1 hour) for misses and `cache_failure_ttl` (1 minute)
for failed lookups (e.g., a service is down; not lookups that only ran
out of time), at most `cache_size` addresses (the least recently used go
first). FOCA 0.18 gives plugins no
database, so the cache is not shared between processes.

Name resolution can't be interrupted, and hosts of inputs come from
clients, so slow or hostile name servers must not tie up the gateway's
plugin workers:

- all host names of a call are resolved in parallel, within one
  `resolve_timeout` (2 seconds), well below FOCA's default step timeout;
- each process resolves at most `resolve_threads` (16) names of inputs
  at a time, and as many names of backends, in threads of their own, so
  that names of inputs can't hold up backends; a call starts at most a
  quarter of the threads for its inputs; further names stay unresolved;
- each name is resolved by one thread at a time; concurrent calls wait
  for it, each until its own deadline;
- the addresses of names, also of names that couldn't be resolved, are
  cached for `resolve_cache_ttl` (5 minutes).

## Configuration

Admin settings, in the gateway app's configuration (FOCA's `plugins`
section):

```yaml
plugins:
  available:
    tes.routing.distance:
      distribution: foca-plugin-tes-routing-distance
      config:
        database: /opt/geoip/dbip-city-lite.mmdb
  pipeline:
    - id: route
      alternatives:
        - plugin: tes.routing.distance
        - plugin: foca.routing.ordered  # if distance routing doesn't apply
```

Configure the locations of your backends in the backend list where you
know them: they are exact, and need no lookup.

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `config.provider` | `offline` or `dbip` | `offline` | where locations of addresses come from (see below) |
| `config.database` | path of an existing file | none; required for `offline` | GeoIP database in MaxMind DB format |
| `config.dbip_url` | `https` URL | `https://api.db-ip.com/v2/free` | DB-IP's API (`dbip`) |
| `config.nominatim_url` | `https` URL | `https://nominatim.openstreetmap.org` | Nominatim, e.g., an instance of your own (`dbip`) |
| `config.user_agent` | visible ASCII and spaces, 1 to 256 characters, not starting with a space | `foca-plugin-tes-routing-distance` | user agent of the requests (`dbip`); Nominatim's usage policy asks for one that identifies your gateway, e.g., with a contact |
| `config.nominatim_interval` | seconds, 0 to 60 | `1.0` | time between requests to Nominatim, per process (its policy: at most 1 per second) |
| `config.dbip_backoff` | seconds, 0 to 86400 | `600` | time to ask DB-IP nothing after it refused a lookup, e.g., over its daily quota (`dbip`) |
| `config.dbip_max_lookups` | 1 to 1000 | `5` | uncached lookups per call, for inputs and for backends each (`dbip`), so that no single task uses up DB-IP's quota |
| `config.locate_backends` | boolean | `true` | whether backends without configured location are located by their address |
| `config.max_hosts` | 1 to 1000 | `20` | input hosts to locate per task |
| `config.resolve_timeout` | seconds, up to 60 | `2.0` | time to resolve the host names of a call, in total: half for inputs, the rest for backends (and never beyond the step's deadline) |
| `config.resolve_threads` | 1 to 256 | `16` | host names resolved at a time, per process, for inputs and for backends each (a call: at most a quarter for its inputs) |
| `config.resolve_cache_ttl` | seconds, ≥ 0 | `300` | time to keep the addresses of host names, also of names that couldn't be resolved |
| `config.cache_size` | ≥ 0 | `10000` | entries of each cache (addresses; host names of inputs and of backends; with `dbip`, places and coordinates); 0 turns the caches off |
| `config.cache_ttl` | seconds, ≥ 0 | `86400` | time to keep located addresses |
| `config.cache_miss_ttl` | seconds, ≥ 0 | `3600` | time to keep addresses without location |
| `config.cache_failure_ttl` | seconds, ≥ 0 | `60` | time to keep addresses whose lookup failed as unlocated |

The plugin has no parameters.

### Offline provider (default): a GeoIP database

The plugin reads a local GeoIP database in [MaxMind DB
format](https://maxmind.github.io/MaxMind-DB/); no data leaves the gateway
(apart from DNS queries for the hosts of inputs, see below). We recommend
DB-IP's free **IP to City Lite** database, under the [Creative Commons
Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/):
[IP Geolocation by DB-IP](https://db-ip.com). Other databases with
GeoIP2-City-style records (`location.latitude`, `location.longitude`)
work, too, e.g., MaxMind's GeoLite2 City (under MaxMind's own licence
terms, which need an account).

Neither this repository nor the package contains a database: download it
from [DB-IP](https://db-ip.com/db/download/ip-to-city-lite) (MMDB format),
check it, and add it to your gateway's image or mount it, e.g., in a
derived image (see "Installing" in the docs):

```dockerfile
FROM docker.io/elixircloud/protes:<version>@sha256:<digest>
COPY plugins.txt /tmp/plugins.txt
RUN pip install --no-cache-dir --no-deps --require-hashes -r /tmp/plugins.txt
# Downloaded and checked before the build, e.g.:
#   curl -fsSLO https://download.db-ip.com/free/dbip-city-lite-2026-10.mmdb.gz
#   gunzip dbip-city-lite-2026-10.mmdb.gz
#   sha256sum dbip-city-lite-2026-10.mmdb  # record it, compare it in review
COPY dbip-city-lite-2026-10.mmdb /opt/geoip/dbip-city-lite.mmdb
```

DB-IP updates the database monthly: rebuild the image (or replace the
mounted file and restart) at least every few months, as addresses move.
The plugin opens the file at startup; a missing or invalid file stops the
app with an error.

Attribution: if you show the results of the plugin to others (e.g., in
your gateway's documentation or service info), keep the attribution "IP
Geolocation by DB-IP" with a link to <https://db-ip.com>, as the licence
requires.

### Online provider (`dbip`): an explicit opt-in

With `provider: dbip`, the plugin asks DB-IP's free API for the place of
each address (city, region, country), then Nominatim (OpenStreetMap) for
the place's coordinates, as proTES did. Requests go through the gateway's
guarded HTTP client (`ctx.http`: `https`, public addresses, the step's
deadline). It needs no database, but it sends data to third parties for
tasks whose addresses aren't cached (see below), is slower, and has rate
limits: DB-IP's free API allows 500 requests per day, and Nominatim's
public instance one per second. The plugin caches places and coordinates,
looks up at most `dbip_max_lookups` uncached addresses per call (for
inputs and for backends each), asks DB-IP only if Nominatim has a turn
before the deadline, and backs off for `dbip_backoff` when DB-IP refuses
lookups. These limits can't keep users who submit many tasks with fresh
public addresses from using up DB-IP's daily quota; distance routing then
doesn't apply (the next alternative routes) until the quota resets. Use
`offline` where that matters, and your own Nominatim instance
(`nominatim_url`) if you can.

DB-IP's page of the free API asks for no attribution; check DB-IP's terms
of service for your use. Nominatim's data is © OpenStreetMap contributors
(ODbL); the plugin uses the coordinates of places only to rank backends.

## Data transfers

- **Default (`offline`):** none to third parties. The system resolver of
  the gateway resolves the host names of inputs and backends (DNS queries,
  as for any connection).
- **`provider: dbip`:**
  - **db-ip.com** (`api.db-ip.com`) gets the public IP addresses of the
    hosts of task inputs and of backends without configured location;
  - **Nominatim** (by default `nominatim.openstreetmap.org`) gets the city,
    region and country of those addresses.

  The hosts of inputs may reveal where users keep their data: tell users,
  e.g., in your gateway's privacy notice. Clients see these transfers in
  the plugin listing (`data_transfers`), whatever the provider.

## Installation

```bash
pip install foca-plugin-tes-routing-distance
```

See "Installing" in the docs for release trains and derived images.

## Development

From the repository root, with the development environment set up (see
"Contributing" in the docs):

```bash
uv run ruff check plugins/tes/routing/distance
uv run python -m scripts.packages mypy plugins/tes/routing/distance
uv run coverage run --source plugins/tes/routing/distance/src \
  -m pytest plugins/tes/routing/distance
uv run coverage report
```

The tests write their own small GeoIP database (`mmdb-writer`, a
development dependency) and need no network.

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
