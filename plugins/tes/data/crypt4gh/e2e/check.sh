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
# `/tmp/foca-plugins-e2e`; removed and recreated), `E2E_PORT`, the port of
# proTES on the host's loopback interface (default 18080), and
# `E2E_WAIT`, the seconds to wait for each task (default 180).

set -euo pipefail

e2e="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin="$(dirname "${e2e}")"
root="$(cd "${plugin}/../../../.." && pwd)"
export E2E_WORK_DIR="${E2E_WORK_DIR:-/tmp/foca-plugins-e2e}"
export E2E_PORT="${E2E_PORT:-18080}"
export COMPOSE_FILE="${e2e}/compose.yaml"
api="http://127.0.0.1:${E2E_PORT}/ga4gh/tes/v1"
wait="${E2E_WAIT:-180}"
# The image of the plugin's config (`config.yaml`)
decrypt_image=foca-plugin-tes-data-crypt4gh-decrypt:e2e
# The image of the task's own executors (as in proTES's integration test)
image='alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f'
# The content of the Crypt4GH file
plaintext="hello crypt4gh"

case "${E2E_WORK_DIR}" in
  /?*) ;;
  *)
    echo "E2E_WORK_DIR must be an absolute path, not ${E2E_WORK_DIR}" >&2
    exit 2
    ;;
esac

fail() {
  echo "::error::$1" >&2
  exit 1
}

# The files that containers wrote are root's: removed by a container, too
remove_work_dir() {
  if [[ -d "${E2E_WORK_DIR}" ]]; then
    docker run --rm -v "${E2E_WORK_DIR}:/work" "${image}" \
      sh -c 'rm -rf /work/* /work/.[!.]*' || true
    rm -rf "${E2E_WORK_DIR}"
  fi
}

cleanup() {
  local status=$?
  if [[ "${status}" != 0 ]]; then
    docker compose logs --no-color protes protes-worker funnel || true
  fi
  docker compose down --volumes --remove-orphans || true
  docker ps -aq --filter label=foca-plugins-e2e=task | xargs -r docker rm -f || true
  remove_work_dir
  rm -f "${body}"
  exit "${status}"
}
body="$(mktemp)"
trap cleanup EXIT

remove_work_dir
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

# An access token of the identity provider
token() {
  docker compose exec -T services python -c \
    "import json, urllib.request; \
    print(json.load(urllib.request.urlopen( \
    'http://localhost:8080/oidc/token', timeout=60))['access_token'])"
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

# Wait until a task has a state
await_state() {
  local state=""
  for _ in $(seq "${wait}"); do
    check GET "/tasks/$1?view=MINIMAL" 200 '.state != null'
    state="$(jq -r .state "${body}")"
    [[ "${state}" == "$2" ]] && return
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
  docker compose exec -T services python -c \
    "import json, sys, urllib.request; \
    print(json.dumps(json.load(urllib.request.urlopen(sys.argv[1], timeout=60))))" \
    "http://funnel:8000/v1/tasks/$1?view=FULL" > "${body}"
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
  local dir
  dir="$(volume "$1")"
  [[ -d "${dir}" ]] || fail "No volume at ${dir}"
  [[ -z "$(ls -A "${dir}")" ]] || fail "$2: $(ls -A "${dir}")"
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
check_backend "${remote}" '
  (.executors | length == 3)
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
# Funnel 0.11.14 runs the executors after one that fails with `ignore_error`,
# but ends the task in `EXECUTOR_ERROR` (as proTES's integration test finds)
await_state "${id}" EXECUTOR_ERROR
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
