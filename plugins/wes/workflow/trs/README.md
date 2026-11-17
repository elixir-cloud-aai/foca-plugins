# wes.workflow.trs

<!-- --8<-- [start:summary] -->
Runs WES workflows registered in a GA4GH Tool Registry Service (TRS): for
runs whose `workflow_url` is a TRS URI (`trs://<host>/<tool>/<version>`) or
the URL of a TRS tool version, fetches the workflow's descriptor files from
an allowed TRS, attaches them to the run and points `workflow_url` to the
primary descriptor, so that every WES backend can run it.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `wes.workflow.trs` |
| Distribution | `foca-plugin-wes-workflow-trs` |
| Import path | `foca_plugins.wes.workflow.trs` |
| APIs | `wes` |
| Phases | `request` |
| Capabilities | `trs-workflows` |
| Maturity | experimental |
| Data transfers | the TRSs at `allowed_hosts`: tool and version identifiers (see [Data transfers](#data-transfers)) |
| Data stored | none |
| FOCA | FOCA's `dev` branch, until FOCA releases the WES run requests of the plugin API (`>=0.22.1.dev0,<0.24`; see [FOCA versions](#foca-versions)) |

## What it does

WES clients that want to run a workflow registered in a
[GA4GH TRS](https://github.com/ga4gh/tool-registry-service-schemas) v2,
e.g., in [TRS-Filer](https://github.com/elixir-cloud-aai/trs-filer), pass
the tool version as `workflow_url`. WES doesn't define TRS URIs, so most
WES backends can't fetch them. This plugin turns such runs into runs with
attached workflows, which every backend supports.

`request`:

1. Recognizes `workflow_url`s that point to a TRS tool version:
   - `trs://<host>/<tool id>/<version id>`, the TRS URI, which resolves to
     `https://<host>/ga4gh/trs/v2/tools/<tool id>/versions/<version id>`;
   - `https://<host>[/<prefix>]/ga4gh/trs/v2/tools/<tool id>/versions/<version id>`,
     the URL of the tool version, also of TRSs below a path prefix.

   Hosts may have a port (`<host>:<port>`). Identifiers with `/`, `#` or
   other reserved characters are URL-encoded, one path segment each
   (e.g., `trs://dockstore.org/%23workflow%2Fgithub.com%2Forg%2Frepo/main`).
   TRS URIs are fetched with `https` only.
2. Maps the run's `workflow_type` to the TRS descriptor type: `CWL`,
   `WDL`, `NFL` (also `Nextflow`), `SMK` (also `Snakemake`) or `GALAXY`,
   ignoring case.
3. Lists the files of the tool version for that type
   (`GET .../{type}/files`), and fetches the primary descriptor and the
   secondary descriptors (`GET .../{type}/descriptor/{relative_path}`, as
   JSON `FileWrapper`s with inline `content`), one after the other. Other
   files (`TEST_FILE`, `CONTAINERFILE`, `OTHER`, and TRS-Filer's
   `IO_SCHEMA`) are not fetched.
4. Adds the descriptors to the run's `workflow_attachment`, after the
   client's files (e.g., a parameters file), with their relative paths as
   filenames (nested directories kept; leading slashes, as some TRSs list
   paths from the root, removed) and the media type `text/plain`, and sets
   `workflow_url` to the primary descriptor's path, as WES defines for
   attached workflows.

Annotations: `trs_host` (the TRS's host), `files` (number of fetched
descriptors) and `bytes` (their total size).

It raises `NotApplicable` (the run goes on unchanged, or the step's next
alternative applies) if `workflow_url` is no TRS URI, e.g., the filename of
an attached workflow or another `https` URL. It rejects the run:

| Status | When |
| --- | --- |
| `400` | The `trs://` URI or TRS URL is malformed (e.g., user info, a query or fragment, a tool or version identifier `.` or `..`), or `workflow_type` has no TRS descriptor type. |
| `403` | The TRS host isn't in `allowed_hosts` (by default, every host). |
| `409` | A fetched file has the name of a file that the client attached (also after NFKC normalization and ignoring case), or is a directory of it, or inside it (e.g., the client attached `tools` and the TRS lists `tools/step.cwl`). |
| `413` | The tool version has more than `max_files` descriptors, or they are larger than `max_size` in total (responses that can't fit are refused before they are parsed). |
| `422` | The TRS has no such tool version with that descriptor type, or lacks a listed descriptor (`404`); there is no single primary descriptor; a path is unsafe (e.g., with `..`), listed twice, or a directory of another; a descriptor has no inline `content` (the plugin doesn't follow `url`s) or no valid text; the file listing is larger than 1 MiB, or a response larger than the gateway's `plugins.defaults.http.max_response_size`. |

Responses are checked for size before they are parsed, so that a TRS
can't make the gateway parse more than about 1 MiB of listing, and
six times `max_size` (JSON escapes) plus 64 KiB of descriptor
responses in total. Other responses of the TRS (e.g.,
`5xx`, `401`, redirects, invalid JSON)
fail the call (`TrsError`), as do errors of the HTTP client (e.g., refused
addresses, timeouts): the step's `on_failure` applies.

All requests go through the gateway's guarded HTTP client (`ctx.http`, cf.
FOCA's `plugins.defaults.http`): `https` only, public addresses only
(unless the operator allows private ones), no redirects, response sizes
bounded, and the whole fetch bounded by the step's `timeout`. Set that
timeout to fit the number of files of the workflows (each file is one
request).

## Configuration

```yaml
plugins:
  available:
    wes.workflow.trs:
      distribution: foca-plugin-wes-workflow-trs
      config:
        allowed_hosts:
          - trs.example.org
        max_files: 100
        max_size: 10485760  # 10 MiB
  pipeline:
    - id: workflow
      timeout: 30  # seconds for all requests of the fetch
      alternatives:
        - plugin: wes.workflow.trs
```

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `allowed_hosts` | list of strings | `[]` | Hosts of the TRSs that workflows may come from, as host names, optionally with a port (e.g., `trs.example.org:8443`; `:443` is the default), compared ignoring case; IPv4 addresses work too, IPv6 addresses don't. Exact matches only: no wildcards, no subdomains. Empty, every TRS URI is rejected. At most 256. |
| `max_files` | integer | `100` | Maximum number of descriptor files of a workflow (1 to 10000). Keep it, with the files that clients attach, within the gateway's `plugins.attachments.max_count`. |
| `max_size` | integer | `10485760` | Maximum total size of the descriptor files of a workflow, in bytes. Keep it, with the files that clients attach, within the gateway's `plugins.attachments.max_size`, which the engine checks after the hook (output beyond it fails the call). |

The plugin has no parameters (`params`).

To reach a TRS at a private address (e.g., in the same cluster), the
operator must allow private addresses for all plugins
(`plugins.defaults.http.allow_private_addresses`); weigh that against the
risk of server-side request forgery by other plugins.

## Data transfers

The TRSs at `allowed_hosts` get the tool and version identifiers of the
TRS URIs that clients submit, and the descriptor type; no credentials,
neither of the client nor of the gateway (private TRSs are not supported).

## FOCA versions

The plugin needs the WES run requests of FOCA's plugin API
(`foca.plugins.wes`, `Attachment`), which are on FOCA's `dev` branch and in
no FOCA release yet. Until then, the repository's development environment
installs FOCA from `dev` (`[tool.uv.sources]` in the root
`pyproject.toml`), and CI tests the plugin with FOCA's `dev` branch only.
Meanwhile, the dependency audit (pip-audit) skips FOCA, as it can't audit
Git sources.
The plugin can't be released before FOCA releases these features; its lower
bound will then become that release.

## Installation

```bash
pip install foca-plugin-wes-workflow-trs
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
