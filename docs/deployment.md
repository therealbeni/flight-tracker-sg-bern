# Deployment

Live at **https://flight.clanker.ch**.

## How traffic gets there

TLS terminates at Cloudflare's edge via a Cloudflare Tunnel (`cloudflared`), the same
one that fronts the rest of clanker.ch (main site, `todo.`, `cloud.`). That tunnel and
its ingress rules live **outside this repo**, at `/home/benja/website/cloudflared/` on
the host:

```
- hostname: flight.clanker.ch
  service: http://flight-tracker-caddy:80
```

`flight-tracker-caddy` is the `caddy` service defined in this repo's `compose.yaml` -
a plain-HTTP reverse proxy (see `Caddyfile`) that forwards to `app:8000`. It joins two
Docker networks: this project's own `default` network (to reach `app`) and the external
`website_webnet` network (so the shared `cloudflared` container can reach it by name).
This mirrors the existing pattern used by the Nextcloud and Vikunja stacks on the same
host - each owns a small Caddy sidecar on `website_webnet` rather than publishing ports
directly on the host.

The DNS CNAME for `flight.clanker.ch` was added with:

```
docker exec cloudflared cloudflared tunnel route dns clanker-website flight.clanker.ch
```

If this ever needs redoing (new tunnel, moved host), that command plus the ingress
entry above and a `docker restart cloudflared` are the only two integration points -
everything else is self-contained in this repo.

**Note:** restarting the shared `cloudflared` container briefly interrupts *all*
clanker.ch hostnames, not just this app, for a few seconds while it reconnects. Only do
this when actually changing the ingress rules.

## Required `.env` (repo root, gitignored, not in version control)

```
POSTGRES_PASSWORD=<random>
SESSION_SECRET=<random, output of: python3 -c "import secrets; print(secrets.token_hex(32))">
BASE_URL=https://flight.clanker.ch
```

`BASE_URL` drives two things: the links embedded in QR codes and password-reset emails,
and whether the session cookie is marked `Secure` (it is, whenever `BASE_URL` starts
with `https://`).

## Bringing the stack up

```
docker compose up -d db app caddy
```

The `flight-tracker` (OGN ingestion) service is deployed separately/already running
continuously - it isn't part of the same up/down lifecycle as the web app, since it must
never go down for app deploys or maintenance. Never run a bare `docker compose down` on
this project; it tears down every service including the tracker. Use
`docker compose stop <service>` / `rm -f <service>` scoped to what's actually changing.

First deploy only: `docker compose exec app python seed.py` to load the club's known
gliders and home airfields. The very first person to sign up at
https://flight.clanker.ch/signup becomes an approved admin automatically; everyone after
that needs that admin's approval.
