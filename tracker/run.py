import os
import sys

CSV_PATH = os.environ.get("CSV_PATH", "lszb_movements.csv")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from models import Airport
from ddb import DeviceDatabase
from flight_tracker import FlightTracker
from csv_logger import CSVLogger
from ogn.client import AprsClient
from ogn.parser import parse, AprsParseError

LSZB = Airport(icao="LSZB", name="Bern Belp", lat=46.9144, lon=7.4990, elevation_m=510.0)
FILTER_RADIUS_KM = 15


def format_event(event):
    label = event.callsign or event.aircraft_id
    parts = [
        f"{event.timestamp.isoformat()}  {event.event:7s}  {label:12s}",
        f"type={event.aircraft_type}",
        f"alt={event.altitude_m}m" if event.altitude_m is not None else "",
        f"spd={event.ground_speed_kmh}km/h" if event.ground_speed_kmh is not None else "",
    ]
    if event.flight_duration_min is not None:
        parts.append(f"duration={event.flight_duration_min}min")
    return "  ".join(p for p in parts if p)


def main():
    ddb = DeviceDatabase()
    print("Loading OGN device database...")
    count = ddb.load()
    print(f"Loaded {count} registrations.")

    tracker = FlightTracker(airport=LSZB, ddb=ddb)
    logger = CSVLogger(CSV_PATH)

    def process_beacon(raw_message):
        try:
            beacon = parse(raw_message)
        except AprsParseError:
            return
        flight_event = tracker.process_beacon(beacon)
        if flight_event:
            logger.log(flight_event)
            print(format_event(flight_event))

    aprs_filter = f"r/{LSZB.lat}/{LSZB.lon}/{FILTER_RADIUS_KM}"
    client = AprsClient(aprs_user="N0CALL", aprs_filter=aprs_filter)
    client.connect()
    print(f"Connected. Logging {LSZB.icao} traffic to {CSV_PATH} (Ctrl+C to stop)")

    try:
        client.run(callback=process_beacon, autoreconnect=True)
    except KeyboardInterrupt:
        print("\nStopped.")
        client.disconnect()
        logger.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
