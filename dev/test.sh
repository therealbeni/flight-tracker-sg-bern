#!/bin/sh
# Runs the whole test suite in Docker - nothing needs to be installed on the host.
#   dev/test.sh                 all tests
#   dev/test.sh -k stress       extra pytest arguments are passed through
set -e
cd "$(dirname "$0")/.."
docker build -q -t flight-tracker-test -f dev/Dockerfile.test . >/dev/null
docker run --rm -v "$PWD":/src -w /src flight-tracker-test python -m pytest -q -p no:cacheprovider tests "$@"
