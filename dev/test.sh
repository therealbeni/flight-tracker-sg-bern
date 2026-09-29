#!/bin/sh
# Runs the whole test suite in Docker - nothing needs to be installed on the host.
#   dev/test.sh                 all tests
#   dev/test.sh -k stress       extra pytest arguments are passed through
#
# App and tracker tests run in separate pytest processes on purpose: both have
# a top-level module called `models` (see tests/tracker/conftest.py).
set -e
cd "$(dirname "$0")/.."
docker build -q -t flight-tracker-test -f dev/Dockerfile.test . >/dev/null
run() { docker run --rm -v "$PWD":/src -w /src flight-tracker-test python -m pytest -q -p no:cacheprovider "$@"; }
run tests/app "$@"
run tests/tracker "$@"
