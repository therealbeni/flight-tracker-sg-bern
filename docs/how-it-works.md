# How it works

The tracker connects to the OGN APRS network with an anonymous `N0CALL` login and
receives every position beacon within 300 km of central Switzerland. Only the club's
aircraft (the `gliders` table) are tracked; the rest of the traffic only shows that the
feed is up.

```
APRS line -> ogn-parser -> Beacon -> FlightDetector -> takeoff/landing events -> sinks
```

| Module | Job |
|---|---|
| `tracker/run.py` | Wiring: connects to OGN, restores open flights after a restart, logs a health line every 10 min. |
| `flight_tracker.py` | Converts parser output to `Beacon`s, runs the periodic sweep, hands events to every sink (one failing sink never affects the others). Also the raw beacon recorder. |
| `detection.py` | The takeoff/landing state machine. Pure logic, no I/O - tested beacon by beacon. |
| `db_sink.py` | Writes club glider flights into the web app's database and matches QR claims to takeoffs. |
| `airports.py` | "Which airfield is this?" - nearest real airfield within 3 km (OurAirports data, heliports/closed fields excluded). |
| `terrain.py` | Ground elevation (SRTM) for height above ground. Tiles download in the background. |

## Detection

Real OGN data is dirty: GPS altitude jumps by 30 m on the ground, speed flickers,
beacons arrive twice (several receivers) or late, receivers lose aircraft close to the
ground, and pilots switch FLARM off right after landing. So no single beacon ever
changes an aircraft's state. The rules (all in `DetectionRules`):

**Every beacon is classified** as *clearly flying* (>= 60 km/h, or >= 100 m above
ground), *clearly on the ground* (< 30 km/h and < 40 m above ground) or *unsure*
(the gap in between, e.g. a glider rolling out at 45 km/h). Unsure beacons never change
state on their own.

**Takeoff** starts with the first clearly-flying beacon (that is the takeoff time), but
only counts once the aircraft has climbed at least 50 m above ground on two beacons in a
row. A car towing a glider along the runway or a trailer on the motorway never climbs,
so it never becomes a flight.

**Landing** needs clearly-on-the-ground beacons spanning at least 20 s (60 s if there is
no airfield nearby - a glider scraping along a slope in strong wind must not become an
"outlanding"). The landing time is the first low beacon, i.e. the touchdown, not the end
of the rollout. A single fast beacon during the rollout is treated as a GPS glitch;
climbing away again (touch-and-go, go-around) cancels the landing.

**Signal loss.** An airborne aircraft that goes silent while low (below 300 m) is
considered landed after 10 minutes, with an *estimated* landing time (when it was last
seen). Silent while high: only after 5 hours (cross-country out of coverage). If the
aircraft reappears on the ground after being silent in the air, the landing is also
estimated. Silence caused by our own connection being down is not counted.

**First seen in the air** (e.g. took off out of coverage, or the tracker just started):
the flight gets an *estimated* takeoff.

**Tows.** A glider taking off within 60 s of a tow plane or motor glider at the same
airfield is linked to it as an aerotow (the closest in time if there are several). The
tow's flight becomes Flugart F, billed with the glider, as in Vereinsflieger.
A glider is launched by a tow plane or the winch: if no tow plane took off with it, it
gets Startart Winde - three minutes after its takeoff (a tow plane's takeoff is reported
once it has climbed 50 m, so it can come in a little after the glider's), or at its
landing at the latest. Until then the Startart stays empty. A takeoff the tracker didn't
see itself (estimated time) gets no Startart: it may have been towed out of sight.
Members' private aircraft are tracked like the club's; a private motor glider can be
the tow plane too.

**Restarts.** Flights still open in the database are re-attached at startup, so their
landing completes the same record.

Estimated times are flagged (`takeoff_estimated` / `landing_estimated`) so pilots can
check and correct them. Flights under 1 minute with seen takeoff and landing are dropped
as ground movements.

**Tracks.** Every beacon of a club aircraft also goes to the track recorder
(`track_recorder.py`). It keeps the last few minutes per aircraft, so when a takeoff is
reported (only once the aircraft has climbed away) the track starts with the ground
roll. While in the air, positions are saved every 5 s to `track_points` (the live map
reads them); at the landing they're packed into `flight_tracks` and deleted. A flight
dropped as too short takes its track with it.

## Testing and replay

`tests/tracker/test_detection.py` simulates one real-world situation per test (see
`tests/tracker/sim.py`), and `test_detection_stress.py` runs hundreds of randomised
flying days with noise, spikes, lost/duplicate/late beacons. Run `dev/test.sh`.

The live tracker records the raw beacons of club gliders to `data/raw/{date}.aprs`. To
see what the detector makes of a recorded day - e.g. after changing a threshold:

```bash
docker compose run --rm flight-tracker python replay.py /data/raw/2026-09-29.aprs
```

Flights from before tracks were recorded can get theirs from the same files (only
landed flights without a track; running it twice changes nothing):

```bash
docker compose exec flight-tracker python backfill_tracks.py /data/raw/*.aprs
```
