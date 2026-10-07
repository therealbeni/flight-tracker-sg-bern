"""Records the track of every flight of a club aircraft (shared/tracks.py):
positions while in the air for the live map, packed per flight at landing
for the replay.

Positions come from the same beacons the detector sees. A takeoff is only
reported once the aircraft has climbed away, so the last few minutes of
every aircraft are kept in memory: when its flight starts, the ground roll
and first climb are taken from there (needed for the release height, too).
"""

from __future__ import annotations

import sys
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from detection import Beacon, EventKind, FlightEvent, FlightRecord
from shared import tracks
from shared.models import Flight, TrackPoint
from shared.tracks import Point

# Positions before the reported takeoff that still belong to the flight.
LEAD = timedelta(seconds=60)
RECENT = timedelta(minutes=4)
# Beacons further off the stream clock are replays or broken clocks.
MAX_SKEW = timedelta(minutes=5)
# Not saved for this long (database down): only the newest are kept.
MAX_PENDING = 20000


class TrackRecorder:
    def __init__(self, session_factory: sessionmaker, open_flight: Callable[[str], Optional[FlightRecord]],
                 terrain: Callable[[float, float], Optional[float]], flush_every_s: float = 5.0):
        self._session_factory = session_factory
        self._open_flight = open_flight
        self._terrain = terrain
        self._flush_every = flush_every_s
        self._next_flush = 0.0
        self._recent: dict[str, deque[Point]] = {}  # address -> last minutes
        self._pending: dict[str, list[Point]] = {}  # record_id -> not saved yet
        self._flight_ids: dict[str, int] = {}  # record_id -> flights.id

    def observe(self, beacon: Beacon) -> None:
        if abs(beacon.received_at - beacon.timestamp) > MAX_SKEW:
            return
        recent = self._recent.setdefault(beacon.address, deque())
        if recent and beacon.timestamp <= recent[-1].time:
            return  # duplicate or out of order
        point = Point(time=beacon.timestamp, latitude=beacon.latitude, longitude=beacon.longitude,
                      altitude_m=beacon.altitude_m, ground_m=self._ground(beacon), speed_kmh=beacon.ground_speed_kmh,
                      climb_ms=beacon.climb_rate_ms)
        recent.append(point)
        while recent and recent[0].time < point.time - RECENT:
            recent.popleft()

        flight = self._open_flight(beacon.address)
        if flight is not None:
            if flight.record_id not in self._pending and flight.record_id not in self._flight_ids:
                # Just took off (or the tracker restarted): from shortly before the takeoff.
                self._pending[flight.record_id] = [p for p in recent if p.time >= flight.takeoff_time - LEAD]
            else:
                pending = self._pending.setdefault(flight.record_id, [])
                pending.append(point)
                del pending[:-MAX_PENDING]
        if time.monotonic() >= self._next_flush:
            self.flush()

    def _ground(self, beacon: Beacon) -> Optional[float]:
        try:
            return self._terrain(beacon.latitude, beacon.longitude)
        except Exception:  # noqa: BLE001 - no terrain data here: the point still counts
            return None

    def flush(self) -> None:
        """Saves the waiting positions of flights the database knows already."""
        self._next_flush = time.monotonic() + self._flush_every
        if not any(self._pending.values()):
            return
        try:
            with self._session_factory() as db:
                for record_id, points in self._pending.items():
                    flight_id = self._flight_id(db, record_id)
                    if flight_id is not None and points:
                        tracks.store(db, flight_id, points)
                        points.clear()
                db.commit()
        except Exception as exc:  # noqa: BLE001 - try again with the next flush
            print(f"Could not save track points: {exc}", file=sys.stderr)

    def _flight_id(self, db, record_id: str) -> Optional[int]:
        if record_id not in self._flight_ids:
            found = db.scalar(select(Flight.id).where(Flight.record_id == record_id))
            if found is None:
                return None
            self._flight_ids[record_id] = found
        return self._flight_ids[record_id]

    def handle(self, event: FlightEvent) -> None:
        """At the landing: save the rest and pack the track."""
        if event.kind is not EventKind.LANDING:
            return
        record_id = event.flight.record_id
        self.flush()
        with self._session_factory() as db:
            flight_id = self._flight_id(db, record_id)
            if flight_id is not None and db.get(Flight, flight_id) is not None:
                tracks.pack_flight(db, flight_id)
                db.commit()
        # Gone either way: packed, or the flight was dropped (too short).
        self._pending.pop(record_id, None)
        self._flight_ids.pop(record_id, None)


def pack_landed(session_factory: sessionmaker) -> int:
    """Packs tracks left over from flights that landed while the tracker was
    down (or stopped mid-way). Run at startup; returns how many."""
    with session_factory() as db:
        ids = db.scalars(select(TrackPoint.flight_id).join(Flight, Flight.id == TrackPoint.flight_id)
                         .where(Flight.landing_time.is_not(None)).distinct()).all()
        for flight_id in ids:
            tracks.pack_flight(db, flight_id)
        db.commit()
        return len(ids)
