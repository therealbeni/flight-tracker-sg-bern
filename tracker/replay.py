"""Replays recorded raw beacons through the detector and prints the flights.

    python replay.py data/raw/2026-09-29.aprs [more files...]

Uses the same detection code, terrain data and airfields as the live tracker,
but writes nothing: use it to check what the tracker would detect for a day,
e.g. after changing DetectionRules. Recordings are made by the live tracker
(RawRecorder, club gliders only) as "<received-at ISO time> <APRS line>".
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ogn.parser import AprsParseError, parse

from airports import AirportDirectory
from detection import EventKind, FlightDetector
from flight_tracker import Tracker, beacon_from_ogn
from terrain import Terrain


class PrintSink:
    def handle(self, event) -> None:
        if event.kind is not EventKind.LANDING:
            return
        f = event.flight
        minutes = f.duration.total_seconds() / 60
        print(
            f"{f.registration or f.address:8} "
            f"{f.takeoff_airport.icao if f.takeoff_airport else '?':>7} {f.takeoff_time:%Y-%m-%d %H:%M:%S}{'~' if f.takeoff_estimated else ' '} -> "
            f"{f.landing_airport.icao if f.landing_airport else '?':>7} {f.landing_time:%H:%M:%S}{'~' if f.landing_estimated else ' '} "
            f"{minutes:6.1f} min  max {f.max_height_m:5.0f} m AGL"
        )


def main(paths: list[str]) -> None:
    airports = AirportDirectory.from_csv(os.path.join(os.path.dirname(__file__), "src", "airports.csv"))
    detector = FlightDetector(terrain=Terrain(background=False).elevation, nearest_airport=airports.nearest)
    tracker = Tracker(detector, [PrintSink()])
    last = None
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                received, _, message = line.rstrip("\n").partition(" ")
                try:
                    parsed = parse(message)
                except (ValueError, AprsParseError):
                    continue
                last = datetime.fromisoformat(received)
                beacon = beacon_from_ogn(parsed, last)
                if beacon is not None:
                    tracker.process(beacon)
    if last is not None:
        # End of recording: let the silence timers run out, like the live
        # tracker would, so flights still open get their (estimated) landing.
        # Step in small increments - one big jump would look like our own feed
        # being down, which (correctly) resets all silence timers.
        end = last + timedelta(seconds=detector.rules.lost_high_after_s + 60)
        while last < end:
            last += timedelta(seconds=60)
            for event in detector.sweep(last):
                tracker.dispatch(event)
    print("(~ = estimated, not seen directly)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
