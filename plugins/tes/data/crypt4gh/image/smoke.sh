#!/usr/bin/env bash
# Smoke test of the decryption image (`Dockerfile`), as CI runs it (job
# `smoke`): makes two key pairs and a Crypt4GH file for each with the
# image's `crypt4gh` tools, then runs the decryption with the arguments that
# the plugin's executor gets (several `--key` and `--file` options), with
# the inputs mounted read-only and a read-only root file system, and checks
# the decrypted files and their modes; that the teardown (`--clear`, as the
# plugin's teardown executor runs it) empties the volume, keeps the volume
# itself and files outside it, also when run again; and that a failure
# removes the files decrypted before it. Needs Docker; the image must be
# built.
#
# Usage: image/smoke.sh IMAGE

set -euo pipefail

image="${1:?Usage: smoke.sh IMAGE}"
work="$(mktemp -d)"
# Files that the containers wrote are root's: removed by a container, too
cleanup() {
  docker run --rm -v "${work}:/work" "${image}" rm -rf /work/in /work/vol || true
  rm -rf "${work}"
}
trap cleanup EXIT
mkdir "${work}/in" "${work}/vol"
# The containers run as the image's user (root); the test's files are the
# runner's, so the directories are writable for all
chmod 0777 "${work}/in" "${work}/vol"

docker run --rm -v "${work}/in:/in" "${image}" sh -euc '
  for name in alice bob; do
    crypt4gh-keygen --sk "/in/${name}.sec" --pk "/in/${name}.pub" --nocrypt -f
    printf "hello %s\n" "${name}" > "/in/${name}.txt"
    crypt4gh encrypt --recipient_pk "/in/${name}.pub" \
      < "/in/${name}.txt" > "/in/${name}.txt.c4gh"
    rm "/in/${name}.txt"
  done
'

decrypt() {
  docker run --rm --read-only --network none \
    -v "${work}/in:/in:ro" -v "${work}/vol:/vol" "${image}" \
    crypt4gh-decrypt "$@"
}

fail() {
  echo "$1" >&2
  exit 1
}

decrypt --key /in/alice.sec --key /in/bob.sec \
  --file /in/alice.txt.c4gh /vol/crypt4gh/alice.txt \
  --file /in/bob.txt.c4gh /vol/crypt4gh/bob.txt
for name in alice bob; do
  [[ "$(cat "${work}/vol/crypt4gh/${name}.txt")" == "hello ${name}" ]] ||
    fail "Decrypted file of ${name} differs"
  [[ "$(stat -c %a "${work}/vol/crypt4gh/${name}.txt")" == 644 ]] ||
    fail "Decrypted file of ${name} is not readable by all"
done
[[ "$(stat -c %a "${work}/vol/crypt4gh")" == 755 ]] ||
  fail "Directory of the decrypted files is not readable by all"

# The teardown empties the volume: the decrypted files, a temporary file
# left by a stopped decryption, and files derived from the decrypted ones
# (also in directories); a link out of the volume is removed, not
# followed. It keeps the volume and files outside it, and may run again
docker run --rm -v "${work}/vol:/vol" "${image}" sh -euc '
  echo partial > /vol/crypt4gh/.alice.txt.x1y2
  mkdir /vol/crypt4gh/index
  echo derived > /vol/crypt4gh/index/alice.txt.idx
  echo keep > /vol/keep
  ln -s /vol /vol/crypt4gh/link
'
for run in first second; do
  decrypt --clear /vol/crypt4gh || fail "Teardown failed on its ${run} run"
done
[[ -d "${work}/vol/crypt4gh" ]] || fail "Teardown removed the volume"
[[ -z "$(ls -A "${work}/vol/crypt4gh")" ]] || fail "Teardown left files"
[[ -f "${work}/vol/keep" ]] || fail "Teardown removed a file outside the volume"

# The second file is for a key that isn't given: the decryption fails, and
# the first file, decrypted before, is removed
if decrypt --key /in/alice.sec \
  --file /in/alice.txt.c4gh /vol/other/alice.txt \
  --file /in/bob.txt.c4gh /vol/other/bob.txt; then
  fail "Decryption without the key succeeded"
fi
[[ -z "$(ls -A "${work}/vol/other")" ]] || fail "Failed decryption left files"
echo "Smoke test passed"
