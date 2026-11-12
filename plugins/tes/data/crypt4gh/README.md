# tes.data.crypt4gh

<!-- --8<-- [start:summary] -->
Decrypts the [Crypt4GH](https://www.ga4gh.org/news_item/crypt4gh-a-secure-method-for-sharing-human-genetic-data/)
inputs of TES tasks, which TES implementations can't read themselves: adds
an executor before the task's own ones that decrypts the Crypt4GH inputs
with the secret keys among the task's inputs to a volume of its own, and
points the task's executors to the decrypted files. The successor of the
Crypt4GH middleware of proTES.
<!-- --8<-- [end:summary] -->

| | |
| --- | --- |
| Plugin ID | `tes.data.crypt4gh` |
| Distribution | `foca-plugin-tes-data-crypt4gh` |
| Import path | `foca_plugins.tes.data.crypt4gh` |
| APIs | `tes` |
| Phases | `request` |
| Capabilities | `crypt4gh-decryption` |
| Maturity | experimental |
| Data transfers | none |
| Data stored | none (no plugin store) |
| FOCA | 0.18, 0.19, 0.20, 0.21, 0.22 (`>=0.18.0,<0.23`) |

## What it does

`request`:

1. Finds the Crypt4GH files and the secret keys among the task's `inputs`
   (files only, not `type: DIRECTORY`; a null `type` is a file), by the
   suffixes of their `path`s:
   `encrypted_suffixes` (`.c4gh`) and `key_suffixes` (`.sec`). The gateway
   can't read the inputs, so their names decide; the decryption executor
   checks the files when the task runs.
2. Leaves tasks without Crypt4GH inputs as they are, as well as tasks
   without executors and tasks whose `executors` or `volumes` have the
   wrong type, which the gateway app rejects. Tasks that it changed already
   (they have `volume` once and, as their first executor, exactly the
   decryption executor that it would add) are checked as in step 3, except
   for that volume, but not changed again, so that a task sent through the
   gateway twice gets one decryption executor. This holds for gateways with
   the same `image`, `command` and `volume` only: chain gateways with this
   plugin only if their settings are the same.
3. Otherwise, it rejects the task with `422 Unprocessable Content` if:
   - it has no secret key among its inputs;
   - a Crypt4GH input or key has no absolute, normalized path (e.g.,
     `/in/./reads.fq.c4gh` or `//in/reads.fq.c4gh`), or another input, an
     output or a volume has a relative path;
   - a Crypt4GH input has no name besides its suffix (e.g., `/in/.c4gh`),
     or two decrypt to the same name (e.g., `/a/x.c4gh` and `/b/x.c4gh`);
   - an input, output or volume of the task is the `volume` of the
     decrypted files, inside it or one of its parent directories (e.g.,
     `/vol` or `/`), also in another spelling (e.g., `/vol/x/../crypt4gh`);
     for an output with wildcards, the directory before its first wildcard
     counts (e.g., `/` for `/v*/crypt4gh/*`);
   - an output has the path of a Crypt4GH input, also in another spelling,
     or has wildcards that match one (e.g., `/in/*.c4gh`).
4. Adds `volume` (`/vol/crypt4gh`) to the task's `volumes`, and, as the
   task's first executor, the decryption executor (`image`, `command`), which
   decrypts each Crypt4GH input to `<volume>/<name without suffix>`, e.g.,
   `/in/reads.fq.c4gh` to `/vol/crypt4gh/reads.fq`, with any of the keys.
5. Changes the arguments of the task's executors (`command`, and `stdin`)
   that are the path of a Crypt4GH input, in any spelling of the absolute
   path (e.g., `/in/./reads.fq.c4gh`), to the path of its decrypted file.
   Only whole, absolute arguments are changed: a relative path, or an
   argument such as
   `--in=/in/reads.fq.c4gh` keeps the encrypted file, as do `env` and
   `workdir`.

For example, a task with the inputs `/in/reads.fq.c4gh` and
`/keys/alice.sec` and the executor `align /in/reads.fq.c4gh` gets these
executors:

```json
[
  {
    "image": "<image>",
    "command": ["crypt4gh-decrypt", "--key", "/keys/alice.sec",
                "--file", "/in/reads.fq.c4gh", "/vol/crypt4gh/reads.fq"]
  },
  {"image": "aligner", "command": ["align", "/vol/crypt4gh/reads.fq"]}
]
```

Annotations, counts only: `encrypted_inputs` and `key_inputs`. Other
inputs (e.g., a reference genome) are left where they are.

### The decryption executor

The executor runs `crypt4gh-decrypt` (`decrypt.py` of the package) in the
decryption image. It reads the keys (Crypt4GH or OpenSSH ed25519 secret
keys, **without passphrase**), then decrypts each file with any of them,
writing it to a temporary file next to its target first. Decrypted files
are readable by all (0644, their directory 0755 where the executor may
change it), deliberately: the task's next executors may run as other users
than the decryption executor (e.g., images with a non-root user), and the
volume belongs to the task alone. Inputs
are only read, so they may be mounted read-only. If a key can't be read or
a file can't be decrypted (e.g., no key fits, or it is no Crypt4GH file),
the executor removes the files it decrypted and exits with status 1, so
that the task fails before its own executors run. Existing files at the
targets are replaced, not refused, so that a restarted executor (e.g., a
Kubernetes pod) runs again: the volume belongs to one task, whose
executors run once each, in order.

### The decryption image

The image is built from [`image/Dockerfile`](image/Dockerfile) (Python 3.13,
`crypt4gh` from [`image/requirements.txt`](image/requirements.txt), pinned
with hashes, `linux/amd64`), with the plugin's directory as the build
context:

```bash
docker build -f image/Dockerfile -t <registry>/<name>:<tag> plugins/tes/data/crypt4gh
```

CI builds the image and runs [`image/smoke.sh`](image/smoke.sh) on it, but
**doesn't publish it yet**: build it, scan it and push it to a registry that
your TES backends can pull from, and set `image` to it, best by digest. The
image runs as root, as some TES implementations (e.g., Funnel with host
directories) make the task's volumes writable for root only.

## Configuration

Admin settings, in the gateway app's configuration (FOCA's `plugins`
section), e.g., for proTES (overlays replace lists, so repeat the route
step of your pipeline; the decryption step comes first, as it changes the
task):

```yaml
plugins:
  available:
    tes.data.crypt4gh:
      distribution: foca-plugin-tes-data-crypt4gh
      config:
        image: registry.example.org/crypt4gh-decrypt@sha256:<digest>
  pipeline:
    - id: decrypt
      alternatives:
        - plugin: tes.data.crypt4gh
    - id: route
      alternatives:
        - plugin: generic.routing.random
```

proTES's image doesn't contain the plugin: install it in an image derived
from that of proTES (see "Installing" in the docs, and proTES's "Plugin
images"). In proTES's Helm chart, set the same under `plugins.available`
and `plugins.pipeline`.

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `config.image` | string, 1 to 512 characters, no spaces | none; required | image of the decryption executor (see [The decryption image](#the-decryption-image)) |
| `config.command` | 1 to 16 strings | `["crypt4gh-decrypt"]` | command of the decryption script in the image; the plugin appends its arguments |
| `config.volume` | absolute, normalized path below `/` | `/vol/crypt4gh` | volume of the decrypted files; tasks may not use it or paths in it |
| `config.encrypted_suffixes` | 1 to 16 suffixes (a dot and 1 to 32 characters, no `/` or spaces) | `[".c4gh"]` | suffixes of the names of Crypt4GH inputs; the longest that matches is removed from the decrypted file's name |
| `config.key_suffixes` | as `encrypted_suffixes`; none may end with one of them or the other way round (e.g., `.key.c4gh` and `.c4gh`) | `[".sec"]` | suffixes of the names of secret keys among the inputs |

The plugin has no parameters.

## Keys and decrypted data

The plugin moves no data itself, but the tasks that it changes do:

- **Secret keys are inputs of the task**, as with the middleware: they are
  downloaded by the TES backend, available to all of the task's executors,
  and, if a client sends them as inline `content`, stored with the task by
  the gateway and the backend. Prefer keys made for a single task or
  dataset (re-encrypt the data for them), and pass them by URL from storage
  that only the task can read.
- **Decrypted files are written to the task's volume**, on the backend's
  disks, and are removed with the task's working directory, as the backend
  removes it; the plugin adds no step that removes them earlier.

Use the plugin only with TES backends that you trust with the decrypted
data and the keys, e.g., in a trusted research environment.

## Data transfers

None: the plugin calls no services, and the decryption executor, which
runs on the TES backend, needs no network.

## Changes from the middleware

The plugin replaces the Crypt4GH middleware of proTES
(`protes-middleware-crypt4gh`), which proTES can't load since it adopted
FOCA's plugin engine:

- Only inputs named as Crypt4GH files (`.c4gh`) are decrypted, and only
  keys named as keys (`.sec`) are passed to the executor; other inputs stay
  where they are, instead of being moved to the volume.
- Decrypted files lose their suffix (`reads.fq.c4gh` decrypts to
  `reads.fq`), are readable by all (0644), and are written to the volume;
  inputs are only read, so they may be mounted read-only.
- A failed decryption fails the task (exit status 1), instead of passing
  the encrypted file on.
- The volume is a setting (`/vol/crypt4gh`), not a random path per process,
  and tasks that use it are rejected (`422`), as are tasks with Crypt4GH
  inputs but no key.
- The image is a setting, built from this repository, instead of a fixed
  image.

## Installation

```bash
pip install foca-plugin-tes-data-crypt4gh
```

See "Installing" in the docs for release trains and derived images.

## Development

From the repository root, with the development environment set up (see
"Contributing" in the docs):

```bash
uv run ruff check plugins/tes/data/crypt4gh
uv run python -m scripts.packages mypy plugins/tes/data/crypt4gh
uv run coverage run --source plugins/tes/data/crypt4gh/src \
  -m pytest plugins/tes/data/crypt4gh
uv run coverage report
```

The tests make their keys and Crypt4GH files with `crypt4gh` (a
development dependency), in temporary directories: the repository holds no
keys. With Docker, build the image and run its smoke test as CI does:

```bash
docker build -f plugins/tes/data/crypt4gh/image/Dockerfile \
  -t crypt4gh-decrypt:dev plugins/tes/data/crypt4gh
plugins/tes/data/crypt4gh/image/smoke.sh crypt4gh-decrypt:dev
```

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).
