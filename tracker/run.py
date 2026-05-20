import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ogn.client import AprsClient
from ogn.parser import parse, AprsParseError

from models import Airport, FlightPhaseRules
from ddb import DeviceDatabase
from flight_tracker import GlobalFlightTracker, AirportLogger

AIRPORTS = [
    Airport(icao="LSZB", name="Bern Belp", lat=46.9144, lon=7.4990, elevation_m=510.0),
    Airport(icao="LSTZ", name="Zweisimmen", lat=46.551713, lon=7.381012, elevation_m=935.0),
]

AIRPORTS_CSV = os.path.join(os.path.dirname(__file__), "src", "airports.csv")
csv_path = os.environ.get("CSV_PATH")
OUTPUT_DIR = os.path.dirname(csv_path) if csv_path else "."

ddb = DeviceDatabase()
loaded = ddb.load()
print(f"Loaded {loaded} entries from OGN device database.", file=sys.stderr)

tracker = GlobalFlightTracker(
    airports_csv=AIRPORTS_CSV,
    ddb=ddb,
    phase_rules=FlightPhaseRules(),
    detection_radius_km=5.0,
)
loggers = [AirportLogger(airport=airport, output_dir=OUTPUT_DIR) for airport in AIRPORTS]

def process_beacon(raw_message):
    try:
        beacon = parse(raw_message)
    except ValueError:
        print(f"Unhandled packet: {raw_message!r}", file=sys.stderr)
        return
    except AprsParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return

    # Handle Beacon
    flight_record = tracker.process_beacon(beacon)

    # Log flight to db
    if flight_record:
        for airport_logger in loggers:
            airport_logger.log(flight_record)

    #plot_active_flights(tracker._active_flights)


RECONNECT_DELAY_S = 3

print("Starting. Press Ctrl+C to stop.", file=sys.stderr)
while True:
    client = AprsClient(aprs_user="N0CALL", aprs_filter="r/46.8/8.2/300")
    try:
        client.connect()
        print("Connected. Logging traffic.", file=sys.stderr)
        client.run(callback=process_beacon, autoreconnect=True)
        print(f"Connection lost. Reconnecting in {RECONNECT_DELAY_S}s...", file=sys.stderr)
    except KeyboardInterrupt:
        client.disconnect()
        print("Disconnected.", file=sys.stderr)
        break
    except Exception as e:
        print(f"Error: {e}. Reconnecting in {RECONNECT_DELAY_S}s...", file=sys.stderr)
    time.sleep(RECONNECT_DELAY_S)
