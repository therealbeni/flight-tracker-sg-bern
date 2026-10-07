"""Gives flights recorded before the tracker kept tracks one, from the raw
beacon recordings (DATA_DIR/raw, see RawRecorder):

    python backfill_tracks.py /data/raw/2026-10-03.aprs [more files...]

Only landed flights of aircraft with an OGN id, and only those without a
track yet: running it twice changes nothing. A flight's track is its
aircraft's positions from shortly before the takeoff to shortly after the
landing, as the flight is in the database (corrected times count).
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ogn.parser import AprsParseError, parse
from sqlalchemy import exists, select

from flight_tracker import beacon_from_ogn
from shared import tracks
from shared.database import SessionLocal
from shared.models import Flight, FlightTrack, Glider, TrackPoint
from terrain import Terrain
from track_recorder import LEAD, MAX_SKEW, point_of

AFTER_LANDING = timedelta(seconds=60)


def read(paths: list[str], terrain) -> dict[str, list[tracks.Point]]:
    """Every recorded position, per aircraft, in order."""
    points: dict[str, dict[datetime, tracks.Point]] = defaultdict(dict)
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                received, _, message = line.rstrip("\n").partition(" ")
                try:
                    beacon = beacon_from_ogn(parse(message), datetime.fromisoformat(received))
                except (ValueError, AprsParseError):
                    continue
                if beacon is not None and abs(beacon.received_at - beacon.timestamp) <= MAX_SKEW:
                    points[beacon.address].setdefault(beacon.timestamp, point_of(beacon, terrain))
    return {address: [by_time[t] for t in sorted(by_time)] for address, by_time in points.items()}


def main(paths: list[str]) -> None:
    terrain = Terrain(background=False)
    positions = read(paths, terrain.elevation)
    first = min(p[0].time for p in positions.values())
    last = max(p[-1].time for p in positions.values())
    with SessionLocal() as db:
        flights = db.execute(
            select(Flight, Glider.ogn_device_id).join(Glider, Glider.id == Flight.glider_id)
            .where(Flight.deleted_at.is_(None), Flight.landing_time.is_not(None), Glider.ogn_device_id.is_not(None),
                   Flight.takeoff_time >= first - LEAD, Flight.takeoff_time <= last,
                   ~exists().where(FlightTrack.flight_id == Flight.id),
                   ~exists().where(TrackPoint.flight_id == Flight.id))
            .order_by(Flight.takeoff_time)
        ).all()
        done = 0
        for flight, address in flights:
            start, end = flight.takeoff_time - LEAD, flight.landing_time + AFTER_LANDING
            points = [p for p in positions.get(address.upper(), []) if start <= p.time <= end]
            if len(points) < 2:
                print(f"{flight.id}: no recorded positions")
                continue
            tracks.store(db, flight.id, points)
            db.flush()
            tracks.pack_flight(db, flight.id)
            db.commit()
            done += 1
            print(f"{flight.id}: {len(points)} positions, {flight.takeoff_time:%Y-%m-%d %H:%M} UTC")
        print(f"{done} of {len(flights)} flights got a track.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
