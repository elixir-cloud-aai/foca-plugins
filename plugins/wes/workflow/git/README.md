# wes.workflow.git

<!-- --8<-- [start:summary] -->
Runs WES workflows from public Git repositories: for runs whose
`workflow_url` is the URL of a file at a ref (branch, tag or commit) of a
repository on GitHub, GitLab or Bitbucket, as their web pages show it,
downloads the repository's archive at that ref from an allowed host,
attaches its files to the run and points `workflow_url` to the file, so
that every WES backend can run it.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `wes.workflow.git` |
| Distribution | `foca-plugin-wes-workflow-git` |
| Import path | `foca_plugins.wes.workflow.git` |
| APIs | `wes` |
| Phases | `request` |
| Capabilities | `git-workflows` |
| Maturity | experimental |
| Data transfers | the hosts at `allowed_hosts` (for `github.com`, `codeload.github.com`): repository and ref (see [Data transfers](#data-transfers)) |
| Data stored | none |
| FOCA | FOCA's `dev` branch, until FOCA releases the WES run requests of the plugin API (`>=0.22.1.dev0,<0.24`; see [FOCA versions](#foca-versions)) |

## What it does

WES clients that want to run a workflow from a Git repository pass the URL
of its main file as `workflow_url`. WES doesn't define such URLs, and most
WES backends can't fetch repositories (the URLs point to web pages). This
plugin turns such runs into runs with attached workflows, which every
backend supports.

`request`:

1. Recognizes `workflow_url`s of files at a ref of a repository:

   | Service | URL |
   | --- | --- |
   | GitHub | `https://github.com/<owner>/<repo>/blob/<ref>/<path>` |
   | GitLab | `https://gitlab.com/<namespace>/<project>/-/blob/<ref>/<path>`, with nested groups (`<group>/<subgroup>/<project>`); also at other GitLab hosts that are in `allowed_hosts` (e.g., `https://gitlab.example.org:8443/...`) |
   | Bitbucket Cloud | `https://bitbucket.org/<workspace>/<repo>/src/<ref>/<path>` |

   `<ref>` is a branch, a tag or a commit. A ref ends at the next `/`, so
   refs with `/` (e.g., `feature/x`) must be URL-encoded (`feature%2Fx`);
   the other forms of the services' URLs (e.g., `tree`, `raw`,
   `raw.githubusercontent.com`) are not recognized. Paths may be
   URL-encoded (e.g., `%20` for spaces).
2. Downloads the repository's `tar.gz` archive at the ref, which the
   services serve without redirects:
   `https://codeload.github.com/<owner>/<repo>/tar.gz/<ref>` (GitHub),
   `https://<host>/<namespace>/<project>/-/archive/<ref>/archive.tar.gz`
   (GitLab), `https://bitbucket.org/<workspace>/<repo>/get/<ref>.tar.gz`
   (Bitbucket). No `git` binary is needed.
3. Reads the files of the archive in memory (never on disk): those of the
   whole repository, or, with `attach: directory`, those of the main
   file's directory and its subdirectories (useful for repositories with
   several workflows, as long as a workflow doesn't refer to files outside
   its directory). The top directory of the archive (e.g.,
   `<repo>-<ref>/`) is removed from the paths.
4. Adds the files to the run's `workflow_attachment`, after the client's
   files (e.g., a parameters file), with their relative paths as filenames
   (nested directories kept) and the media type
   `application/octet-stream`, and sets `workflow_url` to the main file's
   relative path (e.g., `workflows/main.cwl`, or `main.cwl` with
   `attach: directory`), as WES defines for attached workflows.

Annotations: `git_host` (the host of the URL), `files` (number of attached
files) and `bytes` (their total size).

It raises `NotApplicable` (the run goes on unchanged, or the step's next
alternative applies) if `workflow_url` is no such URL, e.g., the filename
of an attached workflow, a TRS URI or another `https` URL. It rejects the
run:

| Status | When |
| --- | --- |
| `400` | The URL is malformed: user info, a query or fragment (e.g., `#L10`, `?plain=1`), an invalid owner, repository or ref (e.g., `..`, spaces), or an unsafe path (e.g., with `..`). |
| `403` | The host isn't in `allowed_hosts` (by default, every host). |
| `409` | A fetched file has the name of a file that the client attached (also after NFKC normalization and ignoring case), or is a directory of it, or inside it. |
| `413` | The repository (or directory) has more than `max_files` files, or they are larger than `max_size` in total; the decompressed archive is larger than `max_archive_size`; or the archive is larger than the gateway's `plugins.defaults.http.max_response_size`. |
| `422` | The host has no such public repository or ref (`404`, or GitLab's redirect to its sign-in page for missing projects; private repositories look the same); the main file is not in the attached files; the archive has an entry with an unsafe path (absolute, or with `..` or `.` segments) or a path longer than 4096 characters, or no single top directory; the files to attach include a symbolic or hard link, a special entry (device, FIFO, sparse file), a filename that is no safe attachment filename, or names that conflict (e.g., `README` and `readme`). |

Other answers of the host (e.g., `5xx`, `401`, `403`, other redirects)
and archives that are no valid `tar.gz` files (e.g., with an invalid
header, or without the zero blocks that end `tar` files) fail the call
(`GitError`), as do errors of the HTTP client (e.g., refused addresses,
timeouts): the step's `on_failure` applies.

Archives are untrusted: they are decompressed as a stream whose size is
checked while it is read, so that archive bombs (also huge headers) stop at
`max_archive_size`; the sizes of the files are checked against `max_size`
before they are read. Links are never followed, and entries outside the
files to attach are skipped, but their paths are checked too.

All requests go through the gateway's guarded HTTP client (`ctx.http`, cf.
FOCA's `plugins.defaults.http`): `https` only, public addresses only
(unless the operator allows private ones), no redirects, response sizes
bounded (`max_response_size`, which also bounds the size of the
compressed archives), and the download bounded by the step's `timeout`.
Set that timeout to fit the size of the repositories.

## Configuration

```yaml
plugins:
  available:
    wes.workflow.git:
      distribution: foca-plugin-wes-workflow-git
      config:
        allowed_hosts:
          - github.com
          - gitlab.com
          - bitbucket.org
        attach: repository
        max_files: 100
        max_size: 10485760  # 10 MiB
        max_archive_size: 104857600  # 100 MiB
  pipeline:
    - id: workflow
      timeout: 30  # seconds for the download
      alternatives:
        - plugin: wes.workflow.git
```

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `allowed_hosts` | list of strings | `[]` | Hosts of the repositories that workflows may come from: `github.com`, `gitlab.com`, `bitbucket.org`, or the host of another GitLab instance, optionally with a port (e.g., `gitlab.example.org:8443`; `:443` is the default), compared ignoring case; no IPv6 addresses. Exact matches only: no wildcards, no subdomains. Empty, every repository URL of `github.com`, `gitlab.com` and `bitbucket.org` is rejected (and URLs of other hosts don't apply). At most 256. |
| `attach` | `repository` or `directory` | `repository` | Files to attach: those of the whole repository, or those of the main file's directory and its subdirectories. |
| `max_files` | integer | `100` | Maximum number of files to attach (1 to 10000). Keep it, with the files that clients attach, within the gateway's `plugins.attachments.max_count`. |
| `max_size` | integer | `10485760` | Maximum total size of the files to attach, in bytes. Keep it, with the files that clients attach, within the gateway's `plugins.attachments.max_size`, which the engine checks after the hook (output beyond it fails the call). |
| `max_archive_size` | integer | `104857600` | Maximum size of the decompressed archive (the `tar` stream, with the files that are not attached and the entries' headers), in bytes. It bounds the work of reading an archive; the compressed archive is bounded by the gateway's `plugins.defaults.http.max_response_size` (16 MiB by default). |

The plugin has no parameters (`params`).

Self-hosted GitHub (Enterprise Server) and Bitbucket (Data Center)
instances are not supported. To reach a GitLab instance at a private
address (e.g., in the same cluster), the operator must allow private
addresses for all plugins (`plugins.defaults.http.allow_private_addresses`);
weigh that against the risk of server-side request forgery by other
plugins.

## Data transfers

The hosts at `allowed_hosts` (for `github.com`, its archive host
`codeload.github.com`) get the owner, repository and ref of the URLs that
clients submit (not the path of the file); no credentials, neither of the
client nor of the gateway (private repositories are not supported, as they
would need credentials per client).

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
pip install foca-plugin-wes-workflow-git
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
