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
- Club ops v2 (below): done 2026-09-29, running on staging (flight.clanker.ch).
- Club ops v4 (below): done 2026-10-06, after the first real flying day (03.10.).
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
  (valid until its takeoff, release, checkout or midnight) tied to their account; claiming again supersedes the previous
  unconsumed claim.
- The tracker and web app now share one schema (`shared/` package, used by both Docker
  images) instead of duplicating models. `tracker/src/db_sink.py` is a `FilteredLogger`
  (a sink like the existing `CsvLogger`) that writes flights for
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

## Club ops v2 (2026-09-29) - make it ready to present to the club

Findings from reviewing the Sonnet-built code, and the features requested by Benja.
Order = implementation order; each step is tested and deployed to staging on its own.
**Status: all steps done and deployed to staging on 2026-09-29.**

**Done - tracker overhaul.** New takeoff/landing detector (hysteresis, confirmation,
signal loss, restarts, duplicates), fleet from the database, airfields added
automatically, raw beacon recording + replay. See [How it works](how-it-works.md).

1. **Local time + German UI.** All times were shown in UTC and "today" was the UTC day.
   Everything is shown in Swiss local time now, and the whole UI is German (Swiss
   spelling, "ss"). No translation framework - the club speaks German, one language
   keeps the templates simple.
2. **Claims done right.**
   - *Freigeben* (un-claim): a pilot can release a claim made by mistake.
   - *Für den ganzen Tag* claims: not used up by the first takeoff, valid until the end
     of the day or until released/checked out. Default for tow planes and motor gliders
     (D-EDUY tow pilot claims once in the morning; HB-2377 flies several legs).
   - Aircraft kinds (glider / motor glider / tow plane) on the `gliders` table.
   - Tow flights linked to the glider flight they towed (same airfield, takeoffs within
     a minute): the glider flight gets launch method "F-Schlepp", tow plane, tow pilot
     and tow time - what Vereinsflieger wants.
3. **Test login.** Admins can view the app as any pilot ("Als Pilot ansehen", with a
   banner to switch back), and a non-admin test account exists on staging.
4. **QR scanner in the app.** The claim tab opens the phone camera and scans the QR code
   on the glider (jsQR, bundled - no external service). The list stays as fallback.
5. **Flugbuch (club PC checkout).** A day view modelled on Vereinsflieger's
   Flugdatenerfassung: one row per flight with the same columns (Kennzeichen, Pilot,
   Begleiter, Startart, Start-/Landeort, Start-/Landezeit, Flugzeit, Landungen, Schlepp,
   Bemerkung), editable, "Flug hinzufügen" for flights the tracker can't see, and a
   per-pilot *Abmelden* (check out): confirm your flights of the day and end your day
   claims. Runs on the club PC under a "Startstelle" account (may edit all open flights
   of the day). Times become editable (manual flights, estimated times).
   *Revised 2026-09-30 after comparing with the real Vereinsflieger screens:* the form
   has Vereinsflieger's fields and codes - Startart, Flugart, Pilot/Begleiter (searchable;
   "Unbekannt" or "Gast" opens a name field), Flugdatum, Start/Landung, Start-/Landeort,
   Abrechnungsart, Bemerkungen; no Landungen. The aircraft of a logged flight is fixed.
   F-Schlepp asks for Schleppflugzeug and Schlepppilot (default: the tow plane and whoever
   is checked in on it); tow plane flights are Flugart F, Abrechnungsart Keine.
6. **Misuse + usability.** CSRF protection, login rate limiting, input validation (no
   500s on bad input), authorization tests for every route; a Playwright walk-through of
   every flow on a phone-sized screen (tap targets, wording, no dead ends).

Not possible without the club: sending emails (password reset currently only prints to
the log - needs an SMTP account), Vereinsflieger sync (needs an API key).

## Club ops v4 (2026-10-06) - after the first real flying day

1. **Check in at the club PC.** The FDL (and admins) check pilots without a phone in from
   the Flugbuch (`/flugbuch/einchecken`): pilot, aircraft, next start or whole day; same
   rules as on the phone, incl. asking before taking over. The claim records who made it
   (`glider_claims.claimed_by_id`).
2. **Flugbuch as the FDL's command center.** The FDL's start page is the Flugbuch. The
   flights refresh themselves every 30 s; aircraft in the air are sky-blue rows with
   their minutes counting up. Adding/correcting a flight, Einchecken and Auschecken open
   as dialogs over the table instead of a form always on screen. The old overview
   (aircraft and pilots of the day) is a side panel ("Flugdienst"): docked on screens
   from 1500 px, a drawer on smaller ones.
3. **Calendar** in the Flugbuch: days with flights are green.
4. **Startart Winde** when no tow plane took off with a glider (see How it works).
5. **Private aircraft.** An aircraft with owners (`aircraft_owners`) is a member's own:
   tracked and logged like the club's, owners check in on it, but it's never offered to
   other pilots, never shown as "frei", and never counted as a tow plane. Admins set owners
   under Verwaltung > Flugzeuge (aircraft are editable there now).
6. **Konto** (`/konto`): change name, e-mail (needs the password), password (other sessions
   end); owners find their aircraft's QR code there.

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
