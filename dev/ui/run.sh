#!/bin/sh
# Browser walk-through of the web app on a phone-sized screen (see walkthrough.py).
#   dev/ui/run.sh            the walk-through
#   dev/ui/run.sh perf.py    load times and layout shifts of every page
# Uses the current code, a throwaway database and containers; touches nothing else.
# Screenshots: dev/ui/screens/
set -e
cd "$(dirname "$0")/../.."
OUT="$PWD/dev/ui/out"
rm -rf "$OUT" && mkdir -p "$OUT/screens" && chmod -R 777 "$OUT"
docker build -q -t flight-tracker-app-ui -f app/Dockerfile . >/dev/null
docker build -q -t flight-tracker-browser dev/ui >/dev/null
NET=flight-tracker-ui-$$
docker network create "$NET" >/dev/null
cleanup() { docker rm -f "$NET-app" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1; }
trap cleanup EXIT
docker run -d --name "$NET-app" --network "$NET" -e BASE_URL=http://localhost:8000 \
    -v "$PWD/app:/app" -v "$PWD/shared:/app/shared" -v "$PWD/dev/ui:/ui" -v "$OUT:/out" \
    flight-tracker-app-ui python /ui/serve.py >/dev/null
for i in $(seq 30); do docker exec "$NET-app" python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/login')" 2>/dev/null && break; sleep 1; done
status=0
docker run --rm --network "container:$NET-app" -v "$PWD/dev/ui:/ui" -v "$OUT:/out" --ipc=host flight-tracker-browser \
    python "/ui/${1:-walkthrough.py}" || status=$?
rm -rf dev/ui/screens && mv "$OUT/screens" dev/ui/screens && rm -rf "$OUT"
[ $status -eq 0 ] || docker logs "$NET-app" 2>&1 | tail -20
exit $status
