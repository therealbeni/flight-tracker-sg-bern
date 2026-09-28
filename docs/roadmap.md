# Roadmap: from movement tracker to full club ops platform

## Where we started

The existing `tracker/` service already solves the hardest problem: it listens to live
OGN APRS beacons, classifies each aircraft's ground/air state from speed + AGL (SRTM
elevation), detects takeoff/landing transitions, attributes the nearest airport within a
configurable radius, and writes one CSV row per flight leg. It already generalizes to any
airfield in range, not just LSZB, and needs no manual input to detect a movement.

What's missing is everything pilot-facing: accounts, the QR "claim this glider" flow, a
dashboard, personal logbooks, and Vereinsflieger sync. That's the scope of this roadmap.

## Decisions locked in (2026-09-27)

- **Hosting:** public VPS with HTTPS, so pilots can claim a glider over mobile data from
  any airfield, and corrections/logbook are reachable remotely.
- **Stack:** FastAPI + server-rendered Jinja2/HTMX, one Python codebase, no separate JS
  build pipeline — extends the existing tracker's language/stack rather than forking it.
- **Auth:** pilots self-register (own email/password + working reset flow); an admin must
  approve each new account before login works, so only real club members get in. Not
  proxied through Vereinsflieger.
- **Vereinsflieger:** API credentials (AppKey) not confirmed to exist yet. This is a
  **blocking action item for the club board** — request API access from Vereinsflieger
  support. Until then, the sync integration is built behind an adapter interface so the
  rest of the system doesn't depend on it.

## Status

- Phase 0: done.
- Phase 1: done.
- Phase 2: done.
- Phase 3: on hold - waiting on the club board to confirm/request a Vereinsflieger AppKey.

## Phase 0 — Accounts & data foundation

- PostgreSQL schema: pilots, gliders, airfields, flights, glider claims, audit trail.
- Auth: signup, admin approval gate, login/logout, full password reset flow.
- Admin panel: approve/reject pilots, manage gliders and airfields.
- Replace the stale `webapp/` (schema didn't match current tracker CSV output) with `app/`.
- Docker Compose: add `db` (Postgres) and `app` services alongside the existing tracker.

## Phase 1 — QR claim flow + tracker integration (done)

- Each glider gets a QR code encoding a stable claim token (`/claim/{token}`), rendered
  server-side and viewable/printable from the admin gliders page.
- Pilot scans it on their phone, confirms, and the system opens a "claim" for that glider
  (default 3 hour expiry) tied to their account; claiming again supersedes the previous
  unconsumed claim.
- The tracker and web app now share one schema (`shared/` package, used by both Docker
  images) instead of duplicating models. `tracker/src/db_sink.py` is a `FilteredLogger`
  (same abstraction as the existing `AirportLogger`/`ClubLogger`) that writes flights for
  known club gliders into Postgres and matches a new takeoff to the most recent
  unconsumed, unexpired claim for that glider - consuming it and attaching the pilot.
  A DB write failure logs and moves on rather than crashing the tracker process.
- Unclaimed takeoffs leave `pilot_id` null for manual assignment on the dashboard.
- Flights that turn out too short to be real (under 1 minute) are dropped, and any claim
  they'd consumed is handed back so it can match the pilot's actual next takeoff.

## Phase 2 — Dashboard & personal logbook (done)

- "Today" board for the club PC: all club gliders' flights today, pilot (or
  "unclaimed"), times, airfields, duration, verified status.
- `/flights/{id}` correction page: a pilot can edit/self-assign their own or an
  unclaimed flight; admins can edit and reassign any flight. Every field change is
  written to `flight_audit_entries` (who, when, old -> new value).
- Outlanding handling: a landing with no known airfield within range keeps the raw
  coordinates on the flight (`is_possible_outlanding`) instead of being dropped, shown
  on the dashboard/logbook/detail pages so the pilot can fill in the real place.
- Personal logbook per pilot (`/logbook`): their historical flights and total hours.
- Admin "finalize" page (`/admin/finalize`) locks a day's landed flights (no more edits,
  for anyone including admins, until explicitly unlocked) - this lock is what Phase 3
  will gate Vereinsflieger sync on.

## Phase 3 — Vereinsflieger sync

- Requires: club board obtains a Vereinsflieger API AppKey (blocking, external to us).
- Build a `VereinsfliegerAdapter` that takes a finalized `Flight` and pushes it into
  Flugdatenerfassung via their API, storing the returned external ID on our `Flight` row
  so re-syncs are idempotent (update, not duplicate).
- Must coexist with manual entry: if a pilot who prefers pen-and-paper (or their own
  Vereinsflieger entry) already logged the same flight by hand, detect the collision
  (same glider + overlapping time window) and surface it for a human to resolve instead
  of creating a duplicate.
- Two-way reconciliation report: flights that failed to sync, or that exist in
  Vereinsflieger but not in our system (fully manual entries), surfaced to admins.

## Phase 4 — Expansion

- Scheduling / day roster (who's flying when, instructor assignment).
- Glider and towplane reservation system.
- Notifications (claim reminders, unfinalized-day nagging, sync failures).
- Statistics/reporting (club-wide hours, per-glider utilization).

## Open questions to resolve with the club before Phase 3

1. Does the club already have, or need to request, a Vereinsflieger API AppKey?
2. What's the policy when a pilot never scans the QR code (tow flights, guest pilots,
   check flights) — manual assignment only, or a fallback "unknown pilot" placeholder
   that must be resolved before finalizing the day?
3. Who counts as "admin" for approving new pilot accounts — should this be more than one
   person so approvals don't bottleneck on a single board member?
