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

## Updating (deploying a new version)

1. Back up the database first:
   `docker compose exec -T db pg_dump -U flighttracker -Fc flighttracker > ~/backups/flighttracker-$(date -u +%Y%m%dT%H%M%SZ).dump`
2. Deploy when no club aircraft is in the air (evening): a tracker restart re-attaches
   open flights from the database, but beacons during the restart are lost.
3. `docker compose build app flight-tracker`
4. `docker compose up -d app` first - its entrypoint runs the database migrations
   (`alembic upgrade head`) - then `docker compose up -d flight-tracker`.
5. After a `Caddyfile` change: `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.

Restore a backup: `docker compose exec -T db pg_restore -U flighttracker -d flighttracker --clean < backup.dump`.

## Accounts and roles

- **Pilot**: checks in, sees and corrects own flights, checks out.
- **Admin**: everything, incl. approving pilots, aircraft, airfields, closing days, and
  "Als Pilot ansehen" (see the app exactly as a given pilot does, to test or help).
- **Flugdienstleiter (FDL)** (laptop at the launch point): register an account for it (e.g.
  "Flugdienstleiter LSZB"), then give it this role on Verwaltung > Piloten. It opens on
  an overview of the day (aircraft in the air / checked in / free, pilots present and
  checked out, flights to check), refreshing itself every 30 s. It can edit/add/delete
  all flights of open days, release check-ins and check out any pilot, but can't check
  in itself or manage pilots or aircraft.

Aircraft kinds (Verwaltung > Flugzeuge) matter: tow planes and motor gliders default to
"check in for the whole day", and tows are linked to the glider they towed.

## Before going live on the club server

- `BASE_URL` must be the club's domain before printing QR codes - the codes contain it.
- Password reset e-mails are only printed to the app log until an SMTP account is wired
  up in `app/email_sender.py`.
- The staging server has test accounts (`testpilot@flight.clanker.ch`,
  `startstelle@flight.clanker.ch`) - don't carry them over.

## Checks

- `dev/test.sh` - all automated tests (in Docker).
- `dev/ui/run.sh` - clicks through every flow in a real browser on a phone-sized and a
  desktop screen, including a QR scan through a simulated camera; screenshots land in
  `dev/ui/screens/`. Uses a throwaway database, touches nothing live.
