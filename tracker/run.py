import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ogn.client import AprsClient
from ogn.parser import parse, AprsParseError

from models import Airport, FlightPhaseRules
from ddb import DeviceDatabase
from flight_tracker import GlobalFlightTracker, AirportLogger

LSZB = Airport(icao="LSZB", name="Bern Belp", lat=46.9144, lon=7.4990, elevation_m=510.0)
AIRPORTS_CSV = os.path.join(os.path.dirname(__file__), "src", "airports.csv")
csv_path = os.environ.get("CSV_PATH")
OUTPUT_DIR = os.path.dirname(csv_path) if csv_path else "."
TIMEOUT_CHECK_INTERVAL_S = 30

ddb = DeviceDatabase()
loaded = ddb.load()
print(f"Loaded {loaded} entries from OGN device database.", file=sys.stderr)

tracker = GlobalFlightTracker(
    airports_csv=AIRPORTS_CSV,
    ddb=ddb,
    phase_rules=FlightPhaseRules(),
    detection_radius_km=5.0,
)
logger = AirportLogger(airport=LSZB, output_dir=OUTPUT_DIR)

last_timeout_check = time.time()


def process_beacon(raw_message):
    global last_timeout_check
    try:
        beacon = parse(raw_message)
    except AprsParseError:
        return

    now = time.time()
    if now - last_timeout_check > TIMEOUT_CHECK_INTERVAL_S:
        current_time = beacon.get("timestamp") or datetime.now(timezone.utc)
        for _, flight_record in tracker.check_timeouts(current_time):
            logger.log(flight_record)
        last_timeout_check = now

    result = tracker.process_beacon(beacon)
    if result:
        _, flight_record = result
        logger.log(flight_record)


aprs_filter = f"r/{LSZB.lat}/{LSZB.lon}/15"
client = AprsClient(aprs_user="N0CALL", aprs_filter=aprs_filter)
client.connect()
print(f"Connected. Logging {LSZB.icao} traffic to {OUTPUT_DIR}/ (Ctrl+C to stop)", file=sys.stderr)

try:
    client.run(callback=process_beacon, autoreconnect=True)
except KeyboardInterrupt:
    pass
finally:
    client.disconnect()
    logger.close()
    print("Disconnected.", file=sys.stderr)
