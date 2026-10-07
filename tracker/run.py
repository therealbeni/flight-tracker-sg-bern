"""OGN flight tracker: listens to live OGN beacons and logs the takeoffs and
landings of the club's aircraft.

Outputs:
  - the `flights` table of the web app's database
  - `track_points` / `flight_tracks`: the positions of each flight (track_recorder.py)
  - DATA_DIR/raw/{date}.aprs   raw beacons of club aircraft, for replay.py

Only aircraft in the `gliders` table are tracked. See docs/how-it-works.md.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ogn.client import AprsClient
from ogn.parser import AprsParseError, parse

from airports import AirportDirectory
from db_sink import DbSink
from detection import FlightDetector
from flight_tracker import RawRecorder, Tracker, beacon_from_ogn
from shared.database import SessionLocal
from terrain import Terrain
from track_recorder import TrackRecorder, pack_landed

# Beacons within this many km of the point are received (APRS range filter):
# club aircraft on cross-country flights anywhere in Switzerland.
APRS_FILTER = "r/46.8/8.2/300"
DATA_DIR = os.environ.get("DATA_DIR", ".")
AIRPORTS_CSV = os.path.join(os.path.dirname(__file__), "src", "airports.csv")
STATS_EVERY_S = 600
RECONNECT_DELAY_S = 3


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


airports = AirportDirectory.from_csv(AIRPORTS_CSV)
log(f"Loaded {len(airports)} airfields.")
terrain = Terrain()
terrain.preload(lat_min=46, lat_max=47, lon_min=6, lon_max=8)  # home region, avoids downloads mid-flight

db_sink = DbSink(SessionLocal)


def is_club_aircraft(address: str) -> bool:
    return address in db_sink.fleet()


def aircraft_info(address: str) -> tuple[str, str]:
    return db_sink.fleet().get(address, ""), ""


detector = FlightDetector(terrain=terrain.elevation, nearest_airport=airports.nearest, aircraft_info=aircraft_info)
# The recorder after the database sink: a flight's row exists before its track is saved.
recorder = TrackRecorder(SessionLocal, detector.open_flight, terrain.elevation)
tracker = Tracker(detector, sinks=[db_sink, recorder], track=is_club_aircraft, observers=[recorder])
raw = RawRecorder(os.path.join(DATA_DIR, "raw"), db_sink.fleet)

# Flights that were in the air when the tracker last stopped.
try:
    now = datetime.now(timezone.utc)
    for flight in db_sink.open_flights(timedelta(seconds=detector.rules.lost_high_after_s), airports.get):
        detector.restore(flight, now)
        log(f"Restored open flight of {flight.registration} (took off {flight.takeoff_time:%H:%M} UTC).")
except Exception as exc:  # noqa: BLE001 - start tracking anyway
    log(f"Could not restore open flights: {exc}")
try:
    if packed := pack_landed(SessionLocal):
        log(f"Packed the tracks of {packed} flights that landed while the tracker was down.")
except Exception as exc:  # noqa: BLE001
    log(f"Could not pack leftover tracks: {exc}")

stats = {"beacons": 0, "parse_errors": 0}
next_stats = time.monotonic() + STATS_EVERY_S


def process_line(raw_message: str) -> None:
    global next_stats
    received_at = datetime.now(timezone.utc)
    try:
        parsed = parse(raw_message)
    except (ValueError, AprsParseError):
        # Mostly FANET/receiver status lines the parser doesn't support.
        stats["parse_errors"] += 1
        return
    beacon = beacon_from_ogn(parsed, received_at)
    if beacon is not None:
        stats["beacons"] += 1
        try:
            raw.record(beacon, raw_message)
        except OSError as exc:
            log(f"Could not record raw beacon: {exc}")
        try:
            tracker.process(beacon)
        except Exception as exc:  # noqa: BLE001 - one bad beacon must not drop the connection
            log(f"Error processing {raw_message!r}: {exc!r}")

    if time.monotonic() >= next_stats:
        next_stats = time.monotonic() + STATS_EVERY_S
        airborne = ", ".join(sorted(f.registration or f.address for f in detector.active_flights()))
        log(f"Last {STATS_EVERY_S // 60} min: {stats['beacons']} position beacons, "
            f"{stats['parse_errors']} unparseable lines. Club aircraft in the air: {airborne or 'none'}")
        stats.update(beacons=0, parse_errors=0)


log("Starting. Press Ctrl+C to stop.")
while True:
    client = AprsClient(aprs_user="N0CALL", aprs_filter=APRS_FILTER)
    try:
        client.connect()
        log("Connected. Tracking club aircraft.")
        client.run(callback=process_line, autoreconnect=True)
        log(f"Connection lost. Reconnecting in {RECONNECT_DELAY_S}s...")
    except KeyboardInterrupt:
        client.disconnect()
        log("Disconnected.")
        break
    except Exception as exc:  # noqa: BLE001 - reconnect on anything
        log(f"Error: {exc}. Reconnecting in {RECONNECT_DELAY_S}s...")
    time.sleep(RECONNECT_DELAY_S)
