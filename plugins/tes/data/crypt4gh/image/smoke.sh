#!/usr/bin/env bash
# Smoke test of the decryption image (`Dockerfile`), as CI runs it (job
# `smoke`): makes a key pair and a Crypt4GH file with the image's `crypt4gh`
# tools, then runs the decryption as the plugin's executor does, with the
# inputs mounted read-only and a read-only root file system, and checks the
# decrypted file. Needs Docker; the image must be built.
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
  crypt4gh-keygen --sk /in/alice.sec --pk /in/alice.pub --nocrypt -f
  printf "hello\n" > /in/hello.txt
  crypt4gh encrypt --recipient_pk /in/alice.pub < /in/hello.txt > /in/hello.txt.c4gh
  rm /in/hello.txt
'

docker run --rm --read-only --network none \
  -v "${work}/in:/in:ro" -v "${work}/vol:/vol" "${image}" \
  crypt4gh-decrypt --key /in/alice.sec \
  --file /in/hello.txt.c4gh /vol/crypt4gh/hello.txt

if [[ "$(cat "${work}/vol/crypt4gh/hello.txt")" != "hello" ]]; then
  echo "Decrypted file differs" >&2
  exit 1
fi

# A file that no key decrypts fails, without leaving a decrypted file
docker run --rm -v "${work}/in:/in" "${image}" \
  crypt4gh-keygen --sk /in/bob.sec --pk /in/bob.pub --nocrypt -f
if docker run --rm --read-only --network none \
  -v "${work}/in:/in:ro" -v "${work}/vol:/vol" "${image}" \
  crypt4gh-decrypt --key /in/bob.sec \
  --file /in/hello.txt.c4gh /vol/crypt4gh/other.txt; then
  echo "Decryption with the wrong key succeeded" >&2
  exit 1
fi
if [[ -e "${work}/vol/crypt4gh/other.txt" ]]; then
  echo "Failed decryption left a file" >&2
  exit 1
fi
echo "Smoke test passed"
