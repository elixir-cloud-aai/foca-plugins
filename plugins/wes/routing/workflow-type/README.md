# wes.routing.workflow-type

<!-- --8<-- [start:summary] -->
Routes WES runs to the backends that support their workflow type: keeps
the candidate backends whose service info lists the run's
`workflow_type` and `workflow_type_version`, and its `workflow_engine` and
`workflow_engine_version` if the run sets them, in their order. Service
infos are cached per process; backends whose service info can't be read
are kept or dropped, as the admin configures.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `wes.routing.workflow-type` |
| Distribution | `foca-plugin-wes-routing-workflow-type` |
| Import path | `foca_plugins.wes.routing.workflow_type` |
| APIs | `wes` |
| Phases | `route` |
| Capabilities | `routing-by-workflow-type` |
| Maturity | experimental |
| Data transfers | none (service infos come from the candidate backends, requested without request data; see [Requests](#requests)) |
| Data stored | none (the cache is in the memory of each process) |
| FOCA | FOCA's `dev` branch, until FOCA releases the WES run requests of the plugin API (`>=0.22.1.dev0,<0.24`; see [FOCA versions](#foca-versions)) |

## What it does

WES backends list what they run in their service info
(`GET /ga4gh/wes/v1/service-info`): `workflow_type_versions` (e.g.,
`{"CWL": {"workflow_type_version": ["v1.0", "v1.2"]}}`) and, from WES 1.1
on, `workflow_engine_versions` (e.g.,
`{"cwltool": {"workflow_engine_version": ["3.1"]}}`). A gateway that
sends a run to a backend that doesn't support it only learns that from
the backend's error.

`route`:

1. Reads the run as the client submitted it (`ctx.original`):
   `workflow_type`, `workflow_type_version`, and `workflow_engine` and
   `workflow_engine_version`, if set. Fields that are empty or no strings
   count as not set; `workflow_engine_version` counts only with
   `workflow_engine`.
2. Gets the service info of each candidate backend, from the cache or with
   `GET <backend URL><service_info_path>`, one backend after the other.
3. Keeps the candidates that list
   - the run's `workflow_type`, compared ignoring case, with its
     `workflow_type_version` (if the run sets one), compared exactly;
   - if the run sets `workflow_engine`: that engine, ignoring case, with
     its `workflow_engine_version` (if the run sets one), exactly.

   A type or engine listed without versions (missing or empty list)
   supports every version. Engines of WES 1.0 service infos, whose
   `workflow_engine_versions` map engines to one version string, support
   that version. Backends without `workflow_engine_versions` support no
   engine (also with `workflow_engine_versions: null`). Entries of other
   shapes (e.g., a version string for a workflow type, or a version list
   that is `null`) are skipped (as not listed), and so are
   versions that are no strings; entries of the same name in another case
   are merged.
4. Handles backends whose service info can't be read as `unreadable`
   says: `keep` them in their place (the default), or `drop` them.
   Unreadable are: errors of the request (e.g., refused, timed out
   after `request_timeout`, private address), statuses other than `200`
   (also redirects and `401`), bodies that are no JSON object, service
   infos without `workflow_type_versions`, or with
   `workflow_type_versions` or `workflow_engine_versions` that are no
   objects, and service infos with more than 256 workflow types or
   engines, or more than 256 versions of the types, or of the engines,
   each in all (so that no backend
   makes the cache large); names and versions longer than 64 characters
   are left out. Backends skipped for lack of the step's time (see
   [Requests](#requests)) count as unreadable, too.

The kept candidates are returned in their order, so that a later route
step (e.g., [`generic.routing.random`](../../../generic/routing/random/README.md)
or FOCA's `foca.routing.ordered`) or the gateway's failover orders them.

It raises `NotApplicable` (the candidates stay as they are, or the step's
next alternative applies) if the request is no run request with a
`workflow_type`. It rejects the run with `422` if every candidate's service
info was read and none supports the run; the detail names the fields that
the run sets (e.g., `No backend supports the run's workflow_type,
workflow_type_version`), not their values. If no candidate is left
because unreadable ones were dropped, the plugin returns no candidate and
doesn't reject: one of them may support the run, so the gateway answers
that no backend is available (`503`).

Annotations, counts only: `candidates`, `kept`, `unsupported` (read, but
not supporting the run), `unreadable` (also those skipped), `skipped`
(not requested, or timed out with less than half of `request_timeout`, for lack of the step's time), `fetched`
(service infos requested and answered, or failed) and `cache_hits`. The kept candidates are in the
provenance of each request (`route.candidates_out`).

### Cache

Each process keeps the service info of a backend, by its URL, for
`cache_ttl` seconds (5 minutes), and a backend whose service info couldn't
be read as unreadable for `failure_ttl` seconds (30 seconds), so that its
failures aren't retried on every run; backends skipped for lack of the
step's time aren't cached. Beyond `cache_size` backends, the least recently used go;
`cache_size: 0` (or both TTLs 0) turns the cache off. Changes of a
backend's supports show after `cache_ttl` at the latest. The cache needs
no plugin store, so the plugin works in gateways without a database (e.g.,
proWES).

### Requests

Service infos are requested with the gateway's guarded HTTP client
(`ctx.http`, cf. FOCA's `plugins.defaults.http`): `https` only, public
addresses only, no redirects, response sizes bounded, within the step's
deadline. They carry no credentials, neither of the client
nor of the backend (`Backend.auth`): backends whose service info needs
authentication are unreadable. Backends at private addresses (e.g., in
the same cluster) or with `http` URLs are unreadable, too, unless the
operator allows them for all plugins
(`plugins.defaults.http.allow_private_addresses`, `allow_http`); weigh
that against the risk of server-side request forgery by other plugins.

The plugin needs the gateway's HTTP client: in a context without one
(only in contexts built by hand, e.g., in tests), the call fails
(`RuntimeError`), and the step's `on_failure` applies.

Uncached backends are requested one after the other, each for at most
`request_timeout` seconds, all within the step's `timeout` (FOCA's
`plugins.defaults.timeout`, 10 seconds, unless the step sets one). The
engine discards a hook's result that comes after the step's deadline, so
the plugin keeps the last 0.25 seconds (`RESERVE`) for returning: no
request runs into them. Backends left then, and requests that time out
with less than half of `request_timeout` (as the step's time was nearly
up), are skipped: they count as unreadable for this run, but aren't
cached, so that the next run requests them. Requests that time out with
more time are unreadable and cached for `failure_ttl`. Set the step's
`timeout` to fit `request_timeout` for each backend that may be uncached
at once, plus `RESERVE`.

Service infos larger than `max_size` (256 KiB; at most the gateway's
`plugins.defaults.http.max_response_size`) are refused before they are
parsed, so that parsing them takes no noticeable time: the backend is
unreadable.

## Configuration

```yaml
plugins:
  available:
    wes.routing.workflow-type:
      distribution: foca-plugin-wes-routing-workflow-type
      config:
        unreadable: keep
        cache_ttl: 300
  pipeline:
    - id: workflow-type
      timeout: 10  # seconds for the service infos of uncached backends
      alternatives:
        - plugin: wes.routing.workflow-type
    - id: route
      alternatives:
        - plugin: foca.routing.ordered
```

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `service_info_path` | string | `/ga4gh/wes/v1/service-info` | Path of the service info below each backend's base URL (FOCA's `Backend.url`, without the API's path): an absolute URL path, without query, fragment, `.` or `..` segments, at most 256 characters. |
| `unreadable` | `keep` or `drop` | `keep` | What to do with backends whose service info can't be read: keep them in their place (runs may fail on them, then the gateway's failover goes on), or drop them (runs never go to backends of unknown support, but none may be left). |
| `cache_ttl` | integer | `300` | Seconds to keep the service info of a backend (0 to 86400). |
| `failure_ttl` | integer | `30` | Seconds to keep a backend whose service info couldn't be read as unreadable (0 to 86400). |
| `cache_size` | integer | `1000` | Maximum number of backends in the cache of each process (0 to 10000); 0 turns the cache off. Each backend takes at most about 64 KiB of names and versions (the limits above), so 1000 backends at most about 64 MiB. |
| `request_timeout` | number | `2.0` | Seconds per service info request (more than 0, at most 60), so that one slow backend leaves time for the others; a backend that takes longer is unreadable (and cached as such for `failure_ttl`). |
| `max_size` | integer | `262144` | Maximum size of a service info, in bytes (1024 to 16777216), and at most the gateway's `plugins.defaults.http.max_response_size`; larger ones are unreadable. |

The plugin has no parameters (`params`).

## FOCA versions

The plugin works on WES run requests as FOCA's plugin API defines them
(`foca.plugins.wes`), which are on FOCA's `dev` branch and in no FOCA
release yet. Until then, the repository's development environment
installs FOCA from `dev` (`[tool.uv.sources]` in the root
`pyproject.toml`), and CI tests the plugin with FOCA's `dev` branch only.
Meanwhile, the dependency audit (pip-audit) skips FOCA, as it can't audit
Git sources. The plugin can't be released before FOCA releases these
features; its lower bound will then become that release.

## Installation

```bash
pip install foca-plugin-wes-routing-workflow-type
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
