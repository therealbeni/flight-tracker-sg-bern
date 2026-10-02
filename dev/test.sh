#!/bin/sh
# Runs the whole test suite in Docker - nothing needs to be installed on the host.
# The tests use a throwaway Postgres (same as production: it enforces column
# lengths and row locks, which SQLite silently doesn't).
#   dev/test.sh                 all tests
#   dev/test.sh -k stress       extra pytest arguments are passed through
#   SQLITE=1 dev/test.sh        quicker, on SQLite
set -e
cd "$(dirname "$0")/.."
docker build -q -t flight-tracker-test -f dev/Dockerfile.test . >/dev/null
if [ -n "$SQLITE" ]; then
    docker run --rm -v "$PWD":/src -w /src flight-tracker-test python -m pytest -q -p no:cacheprovider tests "$@"
    exit
fi
NET=flight-tracker-test-$$
docker network create "$NET" >/dev/null
cleanup() { docker rm -f "$NET-db" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1; }
trap cleanup EXIT
docker run -d --name "$NET-db" --network "$NET" --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgres:16-alpine >/dev/null
until docker exec "$NET-db" pg_isready -U test -d test >/dev/null 2>&1; do sleep 1; done
sleep 1  # the image restarts Postgres once after initialising
until docker exec "$NET-db" pg_isready -U test -d test >/dev/null 2>&1; do sleep 1; done
docker run --rm --network "$NET" -e DATABASE_URL=postgresql+psycopg://test:test@"$NET-db":5432/test \
    -v "$PWD":/src -w /src flight-tracker-test python -m pytest -q -p no:cacheprovider tests "$@"
