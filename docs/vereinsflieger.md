# Vereinsflieger push - plan

Status 2026-11-07: stages 0, 1 and 4 done - the client (`app/vereinsflieger.py`, call
log), a read-only look at the club's data (findings below) and one supervised test
flight, created and deleted again. Staging runs in mode `readonly`. Next: stages 2, 3, 5.
The club has created an AppKey (Stammdaten > Einstellungen > REST Interface, 500 calls a
day, status Aktiv). This is Phase 3 of the [roadmap](roadmap.md), the last big step.

## Goal

A button in the Flugbuch (day view) - **"An Vereinsflieger übertragen"** - for the FDL
and admins. It sends the day's flights to the club's real Vereinsflieger
Flugdatenerfassung, so nobody has to type them in again. Pressing it twice, or after a
correction, must never create a flight twice.

## Why this step is different

Everything so far ran on our own server. Vereinsflieger is the club's real system:
flights there are **billed to members' accounts** (Abrechnung), and they count for
licences and statistics. A wrong or duplicated flight costs a member money or
creates work for the treasurer. So:

1. **No write without the user's go.** Every stage below that writes to Vereinsflieger
   is started by Benja, not by Claude on its own. This overrides the general
   "deploy and push freely" permission for this feature.
2. **Staging never writes by accident.** Writing is off unless `VEREINSFLIEGER_MODE=live`
   is set in `.env`. The default is `off`. The staging DB holds backfilled, test and
   pilot-less flights that must never reach Vereinsflieger.
3. **Read before write.** Before pushing a day we list that day in Vereinsflieger and
   skip every flight that is already there (pilots still enter flights by hand today).
4. **Everything is logged.** Every API call (without secrets) goes into a table, so we
   can always answer "what did the tracker send, when, and what came back".
5. **Undo exists.** Every flight we created has its Vereinsflieger flight number
   (`flid`), so it can be deleted again via `flight/delete` (until it's invoiced).
6. **Secrets stay out of git.** AppKey, username and password live only in `.env` on
   the server. (The AppKey was shown in a chat screenshot. Regenerating it once the
   integration runs costs nothing.)

## The API - what we know

Source: the unofficial OpenAPI spec
[Turbo87/vereinsflieger-openapi](https://github.com/Turbo87/vereinsflieger-openapi)
(mirrors the official PDF of 11.09.2026, which needs a Vereinsflieger login; the user
may download it to check details).

- Base URL `https://www.vereinsflieger.de/interface/rest`, form-encoded POST/PUT/DELETE,
  JSON responses.
- Login: `GET auth/accesstoken` -> token; `POST auth/signin` with token, `username`,
  `password` = **md5 of the password**, `appkey`, optionally `auth_secret` (2FA).
  So we need a **Vereinsflieger user login** too, not just the AppKey.
- **500 calls a day per AppKey**, all calls counted (login included). One day of ours is
  about 2-60 flights, so a push costs at most about 70 calls.
- `flight/add` (callsign required) -> `flid`. Fields: `callsign`, `pilotname`/`uidpilot`,
  `attendantname`/`uidattendant`, `starttype` (E/W/F), `departuretime`/`arrivaltime`
  `YYYY-mm-dd HH:ii` (the PDF says UTC - **this club's VF takes local time**, see stage 4), `departurelocation`/`arrivallocation`
  (name, ICAO or `LAT,LON`), `landingcount`, `ftid` (Flugart **as a numeric id from
  the club's master data**, not our letter code), `chargemode` + `uidcharge`, `comment`,
  `towcallsign`, `towpilotname`/`towuidpilot`, `towtime`, `towheight`.
- `flight/edit/{flid}`, `flight/delete/{flid}`, `flight/get/{flid}`,
  `flight/list/date` (one day), `flight/list/modified`, `flight/jointowflights`
  (links a glider flight with its tow flight).
- List entries include `flid`, `flidtow`, `uidpilot`, `pilotname`, `ftid`, `chargemode`,
  `uidcreate`, `createtime`, `invoiced`, `deleted` - enough to learn the club's ids
  from flights that are already there.
- **Not verified:** the response shape of `flight/add` is inferred (not observed live).
  We read every new flight back with `flight/get` once before trusting it.

### Mapping our flight -> Vereinsflieger

| Ours | Vereinsflieger | Note |
|---|---|---|
| `glider.registration` | `callsign` | must exist as an aircraft in VF (HB-xxxx, D-EDUY) |
| `pilot` / `pilot_name` | `uidpilot` + `pilotname` / `pilotname` only | uid from member mapping |
| `companion` / `companion_name` | `uidattendant` / `attendantname` | |
| `launch_method` F/W/E | `starttype` F/W/E | 1:1 |
| `takeoff_time`, `landing_time` | `departuretime`, `arrivaltime` | **local time** (stage 4), minutes |
| `takeoff/landing_airfield_icao` | `departurelocation`/`arrivallocation` | exact VF name ("Bern-Belp Gld LSZB"); outlanding: `LAT,LON` |
| `flight_type` (letter) | `ftid` | **id table from the club's Stammdaten**, see step 1 |
| `billing` | `chargemode` | none=1, pilot=2, companion=3, guest=4, pilot_companion=5, guest_pilot_pays=6, other_member=7 (+`uidcharge`) |
| `notes` | `comment` | |
| tow plane / tow pilot / tow flight duration | `towcallsign`, `towuidpilot`/`towpilotname`, `towtime` | `towheight` from the track later (nice to have) |
| `landings` | `landingcount` | default 1 |

## Findings from the club's real data (stage 1, 2026-11-07)

Read-only, 03.10.-07.11.2026 (104 flights in Vereinsflieger, 87 in the tracker):

- **Login:** the tracker uses the club's shared FDL account (role "FDL", no 2FA). It's
  the account the club PC already enters flights with - in the evening, the FDL types the
  day in (`createtime` ~18:30). Pushed flights look exactly like those. It can't read the
  member list (`user/list` needs "Mitgliederdaten bearbeiten") - good: that list holds
  IBANs and birthdays, which we don't want.
- **The tracker is accurate:** 85 flights matched; takeoff within 1 min for 81, landing
  within 1 min for 81. Flights per day identical on every day at Bern (26/26, 7/7, ...).
  Missing in the tracker: only aircraft not in our fleet (HB-1123, HB-664 and the
  Bellechasse tow plane HB-ORW).
- **Startart wrong when someone else's tow plane tows:** at Bellechasse (03.10.) six
  glider flights were towed by HB-ORW, which we don't track - the tracker said Winde.
  Winch vs tow is billed differently. Fix (tracker): Winde only at an airfield with a
  winch (LSZB); elsewhere leave Startart empty, so the FDL must fill it in.
- **Tows:** a glider flight with `starttype` F-Schlepp and `towcallsign` makes
  Vereinsflieger create the tow plane's flight itself (same `createtime`, linked both
  ways via `flidtow`, Flugart id 3, Abrechnung 8 "Über SF"). So we push **only the glider
  flight with the tow fields**, never the linked tow flight separately. Tow plane flights
  without a glider (transfers) are pushed as their own flights.
- **Times:** the lists show local time (`HH:ii`). (`flight/add` takes local time too,
  see stage 4.)
- **Airfields:** gliders at Bern use "Bern-Belp Gld LSZB", not "Bern-Belp LSZB" (that's
  the airport - two entries exist). Sending "LSZB" may pick the wrong one: we send the
  exact name, from a small table ICAO -> Vereinsflieger name (LSZB, LSTB, LSTZ seen).
- **Flugart ids in use:** 3 (F-Schlepp), 8 (Schulflug), 10 (standard / Privatflug),
  12, 16, 17 - **labels of 12, 16, 17 unknown** (Stammdaten > Flugarten in VF).
- **Members:** pilots and companions come with their VF `uid` in the flights (a few are
  typed as free text, uid 0). Enough to suggest the mapping in stage 2.
- **Duplicates already exist in Vereinsflieger:** some days were entered again by hand
  weeks later (e.g. 03.10. on 19.10. and 22.10.), with flights twice and times "00:00".
  Our push must not add to that: and once the tracker pushes, the club must stop entering
  those days by hand.
- **Matching:** callsign + takeoff within 5 min would miss one real pair (10 min apart
  - the pilot's time vs. ours). Better: same callsign and the flight times overlap (one
  aircraft can't fly twice at once), else takeoff within 15 min.
- **Invoiced flights** (`invoiced` = 1) exist: never edit or delete those.

## Open questions - need Benja or the club before building

1. ~~**Which Vereinsflieger user does the tracker log in as?**~~ Answered: the club's FDL
   account (see findings). Original question: Recommended: a dedicated
   member account "Flugtracker" with only the rights it needs (Flugdatenerfassung; plus
   "Mitgliederdaten bearbeiten" only if we want `user/list`). Then every flight in VF shows
   it was created by the tracker (`uidcreate`), and it can be locked out on its own.
   Needs username + password (and how 2FA is set up on it - ideally off for this account,
   or we need the TOTP secret, not a one-time code).
2. **Is the club OK with a test flight?** The first real write (stage 4) creates one flight,
   reads it back and deletes it again. Tell the treasurer / VF admin beforehand, and do
   it when no billing run is due. Alternative: the first write is a real flight of a real
   flying day, done together with the FDL.
3. ~~**Does a glider flight with `towcallsign` make VF create the tow plane's flight
   itself?**~~ Yes (findings). Original question: Decides whether we push tow flights separately (+ `jointowflights`) or only
   as fields of the glider flight. We find out read-only first (stage 1: look at how
   tow flights in past days were created), and confirm in stage 4.
4. **When is a day pushed?** Recommended: the button is "Tag abschliessen und übertragen" -
   only closed days are pushed, so nothing changes after the push without an admin
   re-opening the day. Corrections after that: admin re-opens, corrects, pushes again
   (= `flight/edit`).
5. **Who keeps entering flights in VF by hand?** As long as pilots also log their own
   flights via the QR tag (addflight.php), we will always skip matches. The club should
   decide when the tracker becomes the one way in, otherwise there is little to push.
6. **Flights without a pilot** (today almost all of them - 87 of 90 on staging have no
   pilot, the club hasn't started checking in yet): we refuse to push them. The FDL must
   fill in the pilot first. (VF would accept a name only, but a flight without a pilot
   can't be billed.)
7. **Members not mapped to a VF member:** push with `pilotname` only, or block?
   Recommended: block until mapped - a name only flight isn't billed to anyone.

## The build, in stages

Each stage is a commit on `feature/vereinsflieger` (branched from `feature/flight-tracks`)
with tests. **Stages 1-3 make only reading calls. Stage 4 onward writes, and only when
Benja says go.**

### Stage 0 - client and safety rails (no network)

- `app/vereinsflieger/client.py`: small `httpx` client. Login (accesstoken -> signin,
  md5 password), one session per push, `signout` at the end, timeouts, no automatic
  retries of writes (a retry after a timeout could create the flight twice - instead we
  look it up, see stage 5).
- Config in `.env`: `VEREINSFLIEGER_MODE` = `off` (default) | `readonly` | `live`,
  `VEREINSFLIEGER_APPKEY`, `VEREINSFLIEGER_USER`, `VEREINSFLIEGER_PASSWORD`,
  optional `VEREINSFLIEGER_CID`, `VEREINSFLIEGER_TOTP_SECRET`. Writing methods raise
  unless mode is `live` - enforced in the client, not just the UI.
- Call budget: every call is counted in the DB per day; the client stops at 400 (leaves
  100 for people using other tools with the same key, e.g. a member's app).
- Table `vereinsflieger_calls`: time, endpoint, our flight id, status, VF error text,
  request fields **without** accesstoken/appkey/password. Shown to admins.
- Tests run against a fake Vereinsflieger (respx/httpx mock), never the real one. A test
  makes sure no test can reach `vereinsflieger.de`.

### Stage 1 - read-only: look at the club's real data (needs: VF login, mode `readonly`)

A command, run by hand: `docker compose exec app python -m vereinsflieger.inspect DAY`.
It logs in and lists one day (`flight/list/date`), about 3 calls each run.

- Run it for the days we tracked (03.10., 04.10., 10.10., ...). Learn:
  - the club's **`ftid` for each Flugart** (N, S, F, ...) and how `chargemode` is used,
  - the **uids and names of members** who flew (for the mapping, without needing
    `user/list` rights),
  - how **tow flights** look (separate flight? `flidtow`? created at the same time by the
    same user = created automatically?) -> answers question 3,
  - what VF calls our airfields (`departurelocation` "LSZB" or "Bern-Belp"?),
  - time rounding.
- **Compare with our tracker** for the same days: which flights does VF have that we
  don't, and the other way round, and how far apart are the times. This is also the real
  data validation of the tracker the roadmap has been waiting for.
- Write the results down in this file (no personal data in git: only ids/codes, no names).

### Stage 2 - mapping (still read-only)

- No new tables: the VF `uid` goes into the existing `pilots.vereinsflieger_member_id`;
  aircraft need no id (VF matches by callsign); the `ftid` table is a dict next to
  `FLIGHT_TYPES` in `shared/models.py`, filled from stage 1.
- Verwaltung > Piloten: field "Vereinsflieger-Mitglied" per pilot, with suggestions:
  members seen in the club's past flights (or from `user/list` if the tracker user may
  read it), matched by name. An admin confirms each one - no automatic matching by name.
- Verwaltung > Flugzeuge: shows whether the callsign exists in VF (seen in past flights).

### Stage 3 - dry run: the button without sending (mode `readonly`)

The Flugbuch gets the button for FDL and admins (closed days only). It opens a dialog,
**"Vorschau: An Vereinsflieger übertragen"**, one row per flight with what would happen:

- **Neu** - would be created (shows the fields as they'd be sent),
- **Schon im Vereinsflieger** - a VF flight with the same callsign and takeoff time
  within 5 min (or same landing time) exists; we take its `flid` and don't send it.
  Shown with what differs (e.g. pilot), for a human to decide,
- **Bereits übertragen** / **Geändert seit der Übertragung** (-> would be edited),
- **Kann nicht übertragen werden** + reason in German: Pilot fehlt, Pilot nicht mit
  Vereinsflieger verknüpft, keine Landung, Startart fehlt, Flugzeug im Vereinsflieger
  unbekannt.

In `readonly` mode the dialog ends there ("Übertragen ist ausgeschaltet"). This runs on
staging with real VF data and costs about 3 calls per preview. Benja checks it against
what's in VF for a few past days.

### Stage 4 - the first write, supervised (Benja's go needed)

With Benja watching, on a day and time agreed with the club (question 2):

1. `flight/add` one flight (a real one of a past day that isn't in VF yet, or a marked
   test flight with comment "Test Flugtracker - wird gelöscht").
2. `flight/get` it: compare every field with what we sent (times, ftid, chargemode,
   tow). Check in the VF web UI that it looks like a hand-entered one.
3. For a towed flight: see whether VF created the tow flight (question 3).
4. Delete it again (`flight/delete`) if it was a test, and check it's gone/marked deleted.
Fix the mapping until a pushed flight is identical to a hand-entered one.

**Done 2026-11-07** (`app/vf_write_test.py`, Benja's go, his account as pilot, Abrechnung
"keine"): flight 17378355 created, read back, deleted - gone from the day list. Learned:

- `flight/add` answers with the whole new flight, `flid` included (no extra lookup).
- **Times are not converted:** we sent 09:00 (meant as UTC), Vereinsflieger stored and
  shows 09:00 - and hand-entered flights show local time. So despite the PDF ("in
  UTC"), this club's Vereinsflieger takes **local time**. We send local time; check
  once more in the VF web UI at the first real push.
- **Airfield by ICAO picks the airport:** "LSZB" became "Bern-Belp LSZB" (and the flight
  Flugmodus "Abflug"). We send the exact names ("Bern-Belp Gld LSZB").
- Startart W -> 5, Flugart 10 and Abrechnung 1 kept, created by the FDL account.

### Stage 5 - the real push

- Migration: the VF `flid` goes into the existing `flights.vereinsflieger_external_id`,
  `vereinsflieger_synced_at` exists too; add `vereinsflieger_error` (last error, German) and
  `vereinsflieger_tow_flid`. Table `vereinsflieger_pushes` (day, who, when, result counts).
- The dialog's "Jetzt übertragen" (mode `live`) runs the push for the day:
  1. Lock: one push at a time per day (Postgres advisory lock / row lock); the button is
     disabled while it runs (double clicks, two FDLs).
  2. Log in, list the day in VF again (the preview may be minutes old).
  3. For each flight in order of takeoff: skip/match/edit/add as in the preview. **Store
     the `flid` and commit immediately after each successful call**, so a crash halfway
     leaves no flight unrecorded.
  4. A timeout or unclear answer on `flight/add`: don't retry; mark the flight
     "unklar", and the next push finds it through the day listing (match by callsign +
     time) instead of creating it again.
  5. Tows according to stage 4: either fields of the glider flight, or the tow plane's
     flight pushed first, then the glider flight, then `jointowflights`.
  6. Sign out. Show the result: "12 übertragen, 3 schon vorhanden, 1 Fehler".
- The Flugbuch shows a small VF mark on each row (übertragen / Fehler / geändert).
- **Deleted flights:** if a pushed flight is deleted in our Flugbuch, it is not deleted
  in VF automatically. The preview lists it ("Im Vereinsflieger löschen?") and an admin
  confirms it one by one.
- Calendar: a day that is closed and fully pushed gets a mark.

### Stage 6 - go live on the club server

- Done by Benja (club server). `.env` there: mode `live`. Staging: back to `off` or
  `readonly` so the two never write to the same club.
- Docs: deployment.md / configuration.md for the new variables, how-it-works.md for
  the push, hilfe.html for the FDL ("So überträgst du den Tag").

## Not now (later, if wanted)

- Pushing automatically at "Tag abschliessen" without a button (only once the button has
  run reliably for weeks).
- Pulling flights entered by hand in VF into our Flugbuch (two-way reconciliation report
  from the roadmap). The preview's "Schon im Vereinsflieger" is the first half of it.
- `towheight` from the tracks, `km` for cross-country flights.

## Tests

- Unit: mapping (every field, UTC rounding, outlanding coordinates, guests, billing
  "Anderes Mitglied", tow fields), matching (time window, two flights of the same glider
  close together), budget stop, mode checks (`off`/`readonly` never write).
- Push against the fake VF: everything new; all already there; crash after flight 3 of 6
  then push again (no duplicates); timeout on add then push again; flight edited after
  push -> edit; double click / two pushes at once -> one runs; VF error per flight shown.
- Authorization: only FDL and admins see/press the button; pilots get 403.
- UI walk-through (Playwright) of the dialog at the club laptop width (1366 px).
- No test can reach the real `vereinsflieger.de` (network guard in conftest).

## What Benja needs to provide

- [x] A VF login for the tracker (the FDL account, in `.env` on staging, mode `readonly`).
- [ ] The labels of Flugart ids 12, 16 and 17 (and the full list, if handy).
- [ ] Answers to questions 2, 4, 5, 6, 7 (recommendations given above).
- [x] The official PDF (checked; matches the spec above).
- Then Claude builds stages 0-3 on its own (read-only), and stops before stage 4.
