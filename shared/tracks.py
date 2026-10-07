"""Flight tracks: the positions of a flight, as the tracker received them.

While a flight is in the air its positions are rows in `track_points`, so the
live map can read the newest ones every few seconds. When it lands they are
packed into one `flight_tracks` row (pack_flight) and the rows deleted:

    columns of whole numbers -> JSON -> zlib       (about 10 bytes a point)

    t   seconds since the first point     lat, lon  degrees * 100 000 (~1 m)
    alt GPS altitude, m above sea level   gnd       terrain below, m (or null)
    v   ground speed, km/h                vz        climb, dm/s (or null)

Used by the tracker (writing) and the web app (reading).
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import FlightTrack, TrackPoint


@dataclass(frozen=True)
class Point:
    time: datetime
    latitude: float
    longitude: float
    altitude_m: float
    ground_m: Optional[float]
    speed_kmh: float
    climb_ms: Optional[float]

    @property
    def height_m(self) -> Optional[float]:
        """Above the ground below."""
        return self.altitude_m - self.ground_m if self.ground_m is not None else None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _round(value: Optional[float], factor: float = 1) -> Optional[int]:
    return None if value is None else int(round(value * factor))


def pack(points: list[Point]) -> bytes:
    start = points[0].time
    columns = {
        "t0": start.isoformat(),
        "t": [int(round((p.time - start).total_seconds())) for p in points],
        "lat": [_round(p.latitude, 1e5) for p in points],
        "lon": [_round(p.longitude, 1e5) for p in points],
        "alt": [_round(p.altitude_m) for p in points],
        "gnd": [_round(p.ground_m) for p in points],
        "v": [_round(p.speed_kmh) for p in points],
        "vz": [_round(p.climb_ms, 10) for p in points],
    }
    return zlib.compress(json.dumps(columns, separators=(",", ":")).encode(), 9)


def unpack(data: bytes) -> list[Point]:
    c = json.loads(zlib.decompress(data))
    start = datetime.fromisoformat(c["t0"])
    return [
        Point(time=start + timedelta(seconds=t), latitude=lat / 1e5, longitude=lon / 1e5, altitude_m=alt,
              ground_m=gnd, speed_kmh=v, climb_ms=None if vz is None else vz / 10)
        for t, lat, lon, alt, gnd, v, vz in zip(c["t"], c["lat"], c["lon"], c["alt"], c["gnd"], c["v"], c["vz"])
    ]


def _from_row(row: TrackPoint) -> Point:
    return Point(time=_aware(row.time), latitude=row.latitude, longitude=row.longitude, altitude_m=row.altitude_m,
                 ground_m=row.ground_m, speed_kmh=row.speed_kmh, climb_ms=row.climb_ms)


def live_points(db: Session, flight_id: int, since: Optional[datetime] = None) -> list[Point]:
    query = select(TrackPoint).where(TrackPoint.flight_id == flight_id)
    if since is not None:
        query = query.where(TrackPoint.time > since)
    return [_from_row(r) for r in db.scalars(query.order_by(TrackPoint.time)).all()]


def last_live_time(db: Session, flight_id: int) -> Optional[datetime]:
    last = db.scalar(select(TrackPoint.time).where(TrackPoint.flight_id == flight_id)
                     .order_by(TrackPoint.time.desc()).limit(1))
    return _aware(last) if last is not None else None


def points_of(db: Session, flight_id: int) -> list[Point]:
    """The flight's whole track so far: packed, or still in the air."""
    packed = db.get(FlightTrack, flight_id)
    points = unpack(packed.data) if packed is not None else []
    newer = live_points(db, flight_id, since=points[-1].time if points else None)
    return points + newer


def pack_flight(db: Session, flight_id: int) -> Optional[FlightTrack]:
    """Packs the flight's live points (and an earlier packed part, if a
    landing was followed by more points) into its FlightTrack. Doesn't commit."""
    points = points_of(db, flight_id)
    if not points:
        return None
    last = points[-1]
    track = db.get(FlightTrack, flight_id) or FlightTrack(flight_id=flight_id)
    track.data, track.point_count = pack(points), len(points)
    track.last_time, track.last_latitude, track.last_longitude, track.last_altitude_m = \
        last.time, last.latitude, last.longitude, last.altitude_m
    db.add(track)
    db.execute(delete(TrackPoint).where(TrackPoint.flight_id == flight_id))
    return track


def store(db: Session, flight_id: int, points: list[Point]) -> None:
    """Live points of a flight in the air (the tracker, every few seconds)."""
    db.add_all(TrackPoint(flight_id=flight_id, time=p.time, latitude=p.latitude, longitude=p.longitude,
                          altitude_m=p.altitude_m, ground_m=p.ground_m, speed_kmh=p.speed_kmh,
                          climb_ms=p.climb_ms) for p in points)


def drop(db: Session, flight_id: int) -> None:
    """A flight that turned out not to be one: its track goes too."""
    db.execute(delete(TrackPoint).where(TrackPoint.flight_id == flight_id))
    db.execute(delete(FlightTrack).where(FlightTrack.flight_id == flight_id))
