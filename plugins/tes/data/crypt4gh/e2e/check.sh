#!/usr/bin/env bash
# End-to-end test of `tes.data.crypt4gh`, as CI runs it (job `e2e`): proTES
# with the plugin, built from this checkout, sends tasks with Crypt4GH inputs
# to Funnel, a real TES backend, in the Compose stack of this directory
# (`compose.yaml`). The inputs (a Crypt4GH file and a secret key, made with
# the decryption image's `crypt4gh` tools) are served over HTTP, and Funnel
# downloads them as it would from any server. Checks that:
#
# 1. a task completes, its executor reads the decrypted content, and the
#    teardown executor leaves the volume of the decrypted files empty;
# 2. a failing decryption (no fitting key for one of two files) fails the
#    task before its own executors run, without decrypted files;
# 3. without `teardown_on_error` (the default), a failing executor of the
#    task stops it before the teardown, which leaves the decrypted file (as
#    documented: the backend removes it with the task's work directory);
# 4. with `teardown_on_error` (proTES restarted with
#    `config-on-error.yaml`), the teardown runs after the failing executor
#    and empties the volume. Funnel supports `ignore_error`; TESK refuses
#    it, so this isn't tested against TESK.
#
# Funnel keeps the work directories of tasks (`funnel.yaml`), so that the
# volume can be checked on the host. Needs Docker with Compose, uv, curl and
# jq; runs from any directory, needs no credentials and nothing outside the
# stack (images are pulled from public registries). Removes the stack, the
# task containers and its work directory when it ends, but not the images.
#
# Usage: e2e/check.sh [IMAGE]
#
# IMAGE: the decryption image to test, e.g., as built by CI's smoke test;
# built from this checkout if not given. Environment: `E2E_WORK_DIR`, an
# absolute path on the host that the Docker daemon can mount (default
# `/tmp/foca-plugins-e2e`; created by the test, and removed when it ends;
# an existing directory is reused only if an earlier run created it),
# `E2E_PORT`, the port of proTES on the host's loopback interface (default
# 18080; also names the Compose project, so that runs of the same checkout
# with different ports and work directories don't interfere; runs of
# different checkouts do, as they share the tags of the images that they
# build), and `E2E_WAIT`, the seconds to wait for each task (default 180).
# Exits with status 2, before it starts anything, if a tool or Docker is
# missing, or if the work directory is not one that it may use.

set -euo pipefail

e2e="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin="$(dirname "${e2e}")"
root="$(cd "${plugin}/../../../.." && pwd)"
E2E_WORK_DIR="${E2E_WORK_DIR:-/tmp/foca-plugins-e2e}"
# Without trailing slashes, as Docker reports the mount sources
while [[ "${E2E_WORK_DIR}" == ?*/ ]]; do
  E2E_WORK_DIR="${E2E_WORK_DIR%/}"
done
export E2E_WORK_DIR
export E2E_PORT="${E2E_PORT:-18080}"
export COMPOSE_FILE="${e2e}/compose.yaml"
export COMPOSE_PROJECT_NAME="foca-plugins-e2e-${E2E_PORT}"
# Marks the work directory as this test's, so that it never empties another
marker="${E2E_WORK_DIR}/.foca-plugins-e2e"
api="http://127.0.0.1:${E2E_PORT}/ga4gh/tes/v1"
wait="${E2E_WAIT:-180}"
# The image of the plugin's config (`config.yaml`)
decrypt_image=foca-plugin-tes-data-crypt4gh-decrypt:e2e
# The image of the task's own executors (as in proTES's integration test)
image='alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f'
# The content of the Crypt4GH file
plaintext="hello crypt4gh"

# An absolute path below `/`, without `:` (which would break the volume
# specifications of Docker)
case "${E2E_WORK_DIR}" in
  *:*)
    echo "E2E_WORK_DIR must not contain ':', not ${E2E_WORK_DIR}" >&2
    exit 2
    ;;
  /*[!/.]*) ;;
  *)
    echo "E2E_WORK_DIR must be an absolute path below /, not ${E2E_WORK_DIR}" >&2
    exit 2
    ;;
esac
# A normalized path (no `.`, `..` or empty components), so that it names
# the directory that is emptied, not one of its parents
if [[ "$(realpath -ms -- "${E2E_WORK_DIR}")" != "${E2E_WORK_DIR}" ]]; then
  echo "E2E_WORK_DIR must be a normalized path, not ${E2E_WORK_DIR}" >&2
  exit 2
fi
# An existing work directory only if an earlier run made it: the runner's
# own, no symbolic link, with the marker
if [[ -L "${E2E_WORK_DIR}" ]] ||
  [[ -e "${E2E_WORK_DIR}" && (! -O "${E2E_WORK_DIR}" || ! -f "${marker}") ]]; then
  echo "E2E_WORK_DIR ${E2E_WORK_DIR} exists, but wasn't made by this test" >&2
  exit 2
fi
# Not inside the work directory of another run, which would remove it
parent="$(dirname "${E2E_WORK_DIR}")"
while [[ "${parent}" != / ]]; do
  if [[ -e "${parent}/.foca-plugins-e2e" ]]; then
    echo "E2E_WORK_DIR ${E2E_WORK_DIR} is inside the work directory of another run (${parent})" >&2
    exit 2
  fi
  parent="$(dirname "${parent}")"
done
for tool in docker uv curl jq; do
  command -v "${tool}" > /dev/null || {
    echo "The end-to-end test needs ${tool}" >&2
    exit 2
  }
done
docker compose version > /dev/null || {
  echo "The end-to-end test needs Docker Compose" >&2
  exit 2
}
docker info > /dev/null || {
  echo "The end-to-end test needs a running Docker daemon" >&2
  exit 2
}

fail() {
  echo "::error::$1" >&2
  exit 1
}

# The files that containers wrote are root's: removed by a container, too.
# Fails if the directory remains
remove_work_dir() {
  if [[ -f "${marker}" ]]; then
    docker run --rm -v "${E2E_WORK_DIR}:/work" "${image}" \
      find /work -mindepth 1 -delete || true
    rm -rf "${E2E_WORK_DIR}" || true
  fi
  [[ ! -e "${E2E_WORK_DIR}" ]]
}

# The task containers of this run (Funnel's, with the label of `funnel.yaml`
# and mounts in this run's work directory)
task_containers() {
  local id mounts
  for id in $(docker ps -aq --filter label=foca-plugins-e2e=task); do
    mounts="$(docker inspect --format \
      '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' "${id}")" || continue
    # Mount sources that start with the work directory
    awk -v dir="${E2E_WORK_DIR}/" 'index($0, dir) == 1 { found = 1 }
      END { exit !found }' <<< "${mounts}" && echo "${id}"
  done
}

# Runs every step, also after errors; keeps the status of a failed test, and
# fails a passed one if something remains
cleanup() {
  local status=$? left=()
  set +e
  if [[ "${status}" != 0 ]]; then
    docker compose logs --no-color protes protes-worker funnel
  fi
  docker compose down --volumes --remove-orphans || left+=("the Compose stack")
  task_containers | xargs -r docker rm -f || left+=("task containers")
  remove_work_dir || left+=("the work directory ${E2E_WORK_DIR}")
  rm -f "${body}"
  if [[ "${#left[@]}" -gt 0 ]]; then
    echo "::error::Cleanup failed, check: ${left[*]}" >&2
    [[ "${status}" != 0 ]] || status=1
  fi
  exit "${status}"
}
body="$(mktemp)"
trap cleanup EXIT

remove_work_dir || fail "Can't remove ${E2E_WORK_DIR}"
mkdir -p "${E2E_WORK_DIR}"
touch "${marker}"
mkdir -p "${E2E_WORK_DIR}/wheels" "${E2E_WORK_DIR}/data" "${E2E_WORK_DIR}/funnel"
# The containers that make the data run as root; the directory is the
# runner's
chmod 0777 "${E2E_WORK_DIR}/data"

echo "Building the decryption image and the wheel of the plugin"
if [[ -n "${1:-}" ]]; then
  docker tag "$1" "${decrypt_image}"
else
  docker build -f "${plugin}/image/Dockerfile" -t "${decrypt_image}" "${plugin}"
fi
uv build --project "${root}" --package foca-plugin-tes-data-crypt4gh --wheel \
  --no-create-gitignore --out-dir "${E2E_WORK_DIR}/wheels"

# Key pairs of alice and bob; `hello.txt.c4gh` for alice, `other.txt.c4gh`
# for bob. Only the secret key of alice is passed to tasks
docker run --rm -v "${E2E_WORK_DIR}/data:/data" -e "PLAINTEXT=${plaintext}" \
  "${decrypt_image}" sh -euc '
    for name in alice bob; do
      crypt4gh-keygen --sk "/data/${name}.sec" --pk "/data/${name}.pub" --nocrypt -f
    done
    printf "%s\n" "${PLAINTEXT}" |
      crypt4gh encrypt --recipient_pk /data/alice.pub > /data/hello.txt.c4gh
    printf "other\n" |
      crypt4gh encrypt --recipient_pk /data/bob.pub > /data/other.txt.c4gh
    chmod 0644 /data/*
  '

start() {
  docker compose up -d --build --wait --wait-timeout 300
}

echo "Starting the stack"
start

# Get a JSON document from a URL in the Compose network (from the container
# of `services.py`, which has Python)
net_get() {
  docker compose exec -T services python -c \
    "import json, sys, urllib.request; \
    print(json.dumps(json.load(urllib.request.urlopen(sys.argv[1], timeout=60))))" \
    "$1"
}

# An access token of the identity provider
token() {
  net_get http://localhost:8080/oidc/token | jq -er .access_token
}
auth="$(token)"

# Request a path of proTES, with an optional JSON body; check the status and
# that the JSON response matches a jq filter
check() {
  local method="$1" path="$2" status="$3" filter="$4" data="${5:-}" actual
  local args=(--silent --show-error --max-time 60 --output "${body}"
    --write-out '%{http_code}' -X "${method}"
    --header 'Accept: application/json'
    --header "Authorization: Bearer ${auth}")
  if [[ -n "${data}" ]]; then
    args+=(--header 'Content-Type: application/json' --data "${data}")
  fi
  actual="$(curl "${args[@]}" "${api}${path}")"
  if [[ "${actual}" != "${status}" ]] ||
    ! jq -e "${filter}" "${body}" > /dev/null; then
    cat "${body}" >&2
    fail "${method} ${path}: status ${actual}, expected ${status} and ${filter}"
  fi
}

# Create a task with the given inputs (names of files of the file server,
# mounted at `/in`, or `/keys` for keys) and executor command (JSON), and
# print its ID
create() {
  local inputs="$1" command="$2"
  check POST /tasks 200 '.id | length > 0' "$(jq -n \
    --arg image "${image}" --argjson names "${inputs}" \
    --argjson command "${command}" '{
      name: "foca-plugins-e2e",
      inputs: [$names[] | {
        url: "http://services:8080/data/\(.)",
        path: (if endswith(".sec") then "/keys/\(.)" else "/in/\(.)" end),
        type: "FILE"
      }],
      executors: [{image: $image, command: $command}]
    }')"
  jq -r .id "${body}"
}

# Wait until a task has a state, or one of several (separated by spaces)
await_state() {
  local state=""
  for _ in $(seq "${wait}"); do
    check GET "/tasks/$1?view=MINIMAL" 200 '.state != null'
    state="$(jq -r .state "${body}")"
    [[ " $2 " == *" ${state} "* ]] && return
    sleep 1
  done
  check GET "/tasks/$1?view=FULL" 200 'true'
  cat "${body}" >&2
  fail "Task $1 is ${state}, not $2"
}

# The ID of a task at Funnel
backend_id() {
  check GET "/tasks/$1?view=BASIC" 200 \
    '.tags["gateway.backend_task_id"] | length > 0'
  jq -r '.tags["gateway.backend_task_id"]' "${body}"
}

# Check the task as Funnel serves it (view `FULL`), from the Compose network,
# against a jq filter (with the decryption image as `$decrypt`)
check_backend() {
  net_get "http://funnel:8000/v1/tasks/$1?view=FULL" > "${body}"
  if ! jq -e --arg decrypt "${decrypt_image}" "$2" "${body}" > /dev/null; then
    cat "${body}" >&2
    fail "Task $1 at Funnel doesn't match $2"
  fi
}

# The volume of the decrypted files of a task at Funnel, on the host (Funnel
# maps the task's paths into its work directory)
volume() {
  echo "${E2E_WORK_DIR}/funnel/$1/vol/crypt4gh"
}

# Check that the volume of a task at Funnel exists and is empty
check_empty() {
  local dir entries
  dir="$(volume "$1")"
  [[ -d "${dir}" ]] || fail "No volume at ${dir}"
  # A listing that fails must not count as empty
  entries="$(ls -A "${dir}")" || fail "Can't list ${dir}"
  [[ -z "${entries}" ]] || fail "$2: ${entries}"
}

# The executors that the plugin adds, as Funnel has them (a jq filter;
# `$decrypt` is jq's)
# shellcheck disable=SC2016
rewritten='
  (.executors | length == 3)
  and .executors[0].image == $decrypt
  and .executors[0].command == ["crypt4gh-decrypt", "--key", "/keys/alice.sec",
    "--file", "/in/hello.txt.c4gh", "/vol/crypt4gh/hello.txt"]
  and .executors[1].command[-1] == "/vol/crypt4gh/hello.txt"
  and .executors[2].image == $decrypt
  and .executors[2].command == ["crypt4gh-decrypt", "--clear", "/vol/crypt4gh"]
  and .volumes == ["/vol/crypt4gh"]'

echo "1. A task with a Crypt4GH input completes with the decrypted content"
id="$(create '["hello.txt.c4gh", "alice.sec"]' '["cat", "/in/hello.txt.c4gh"]')"
await_state "${id}" COMPLETE
remote="$(backend_id "${id}")"
check_backend "${remote}" "${rewritten}
  and .state == \"COMPLETE\"
  and (.logs[0].logs | length == 3)
  and (.logs[0].logs | all(.exit_code == 0))
  and .logs[0].logs[1].stdout == \"${plaintext}\n\""
check_empty "${remote}" "The teardown left files"

echo "2. A failing decryption fails the task without decrypted files"
id="$(create '["hello.txt.c4gh", "other.txt.c4gh", "alice.sec"]' \
  '["cat", "/in/hello.txt.c4gh"]')"
await_state "${id}" EXECUTOR_ERROR
remote="$(backend_id "${id}")"
# `hello.txt.c4gh` is decrypted first, then `other.txt.c4gh` fails: the
# executor removes the file that it decrypted
check_backend "${remote}" '
  (.executors | length == 3)
  and .executors[0].command == ["crypt4gh-decrypt", "--key", "/keys/alice.sec",
    "--file", "/in/hello.txt.c4gh", "/vol/crypt4gh/hello.txt",
    "--file", "/in/other.txt.c4gh", "/vol/crypt4gh/other.txt"]
  and (.logs[0].logs | length == 1)
  and .logs[0].logs[0].exit_code == 1'
check_empty "${remote}" "The failed decryption left files"

echo "3. Without teardown_on_error, a failing executor stops the task before the teardown"
# Reads its argument, the decrypted file, then fails (`$0` is the shell's
# in the executor)
# shellcheck disable=SC2016
fails='["sh", "-c", "cat \"$0\"; exit 3", "/in/hello.txt.c4gh"]'
id="$(create '["hello.txt.c4gh", "alice.sec"]' "${fails}")"
await_state "${id}" EXECUTOR_ERROR
remote="$(backend_id "${id}")"
check_backend "${remote}" "${rewritten}
  and (.executors[1].ignore_error // false) == false
  and (.logs[0].logs | length == 2)
  and .logs[0].logs[1].exit_code == 3
  and .logs[0].logs[1].stdout == \"${plaintext}\n\""
[[ "$(cat "$(volume "${remote}")/hello.txt")" == "${plaintext}" ]] ||
  fail "The decrypted file isn't in the volume"

echo "Restarting proTES with teardown_on_error"
export E2E_OVERLAY=/e2e/config.yaml:/e2e/config-on-error.yaml
start
auth="$(token)"

echo "4. With teardown_on_error, the teardown runs after a failing executor"
id="$(create '["hello.txt.c4gh", "alice.sec"]' "${fails}")"
# The final state is the backend's: Funnel 0.11.14 runs the executors after
# one that fails with `ignore_error`, but ends the task in `EXECUTOR_ERROR`
# (as proTES's integration test finds); TES 1.1, and newer Funnel sources,
# have `COMPLETE`. The executor logs and the volume are what counts
await_state "${id}" "EXECUTOR_ERROR COMPLETE"
remote="$(backend_id "${id}")"
check_backend "${remote}" "${rewritten}
  and .executors[1].ignore_error == true
  and (.executors[0].ignore_error // false) == false
  and (.executors[2].ignore_error // false) == false
  and (.logs[0].logs | length == 3)
  and .logs[0].logs[1].exit_code == 3
  and .logs[0].logs[2].exit_code == 0"
check_empty "${remote}" "The teardown left files"

echo "End-to-end test passed"
