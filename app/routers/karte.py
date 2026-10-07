"""Karte: the club's aircraft in the air right now, and the track of each
flight (replay on its page). The positions come from the tracker
(shared/tracks.py); the pages draw them with Leaflet (static/karte.js).

Points go out as rows [unix time, lat, lon, altitude, ground, km/h, m/s]:
ground and climb can be null.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, FlightTrack, Pilot, TrackPoint
from shared import tracks
from templating import templates
from timeutil import to_local

router = APIRouter()

# A flight still open after this long lost contact long ago (the tracker
# closes those itself): not on the live map.
STALE = timedelta(minutes=20)


def _rows(points: list[tracks.Point]) -> list[list]:
    return [[int(p.time.timestamp()), round(p.latitude, 5), round(p.longitude, 5), round(p.altitude_m),
             None if p.ground_m is None else round(p.ground_m), round(p.speed_kmh),
             None if p.climb_ms is None else round(p.climb_ms, 1)] for p in points]


def _flight_info(flight: Flight) -> dict:
    return {"id": flight.id, "registration": flight.glider.registration if flight.glider else "?",
            "model": flight.glider.model if flight.glider else None, "pilot": flight.pilot_display,
            "takeoff": to_local(flight.takeoff_time).strftime("%H:%M") if flight.takeoff_time else None}


@router.get("/karte")
def karte(request: Request, user: Pilot = Depends(require_approved)):
    return templates.TemplateResponse(request, "karte/live.html", {"pilot": user})


@router.get("/karte/live.json")
def karte_live(seit: Optional[int] = Query(None), bekannt: str = Query(""), db: Session = Depends(get_db),
               user: Pilot = Depends(require_approved)):
    """The flights in the air with their positions. The page asks every few
    seconds with ?seit=<unix time>&bekannt=<flight ids it has>: then only the
    newer positions of those, and the whole track of any new one."""
    now = datetime.now(timezone.utc)
    flights = db.scalars(
        select(Flight).where(Flight.landing_time.is_(None), Flight.deleted_at.is_(None),
                             Flight.takeoff_time > now - timedelta(hours=14),
                             exists().where(TrackPoint.flight_id == Flight.id))
        .order_by(Flight.takeoff_time)
    ).all()
    since = datetime.fromtimestamp(seit, timezone.utc) if seit is not None else None
    known = {int(i) for i in bekannt.split(",") if i.isdigit()}
    aircraft = []
    for flight in flights:
        points = tracks.live_points(db, flight.id, since=since if flight.id in known else None)
        last = points[-1].time if points else tracks.last_live_time(db, flight.id)
        if last is None or now - last > STALE:
            continue
        aircraft.append({**_flight_info(flight), "points": _rows(points)})
    return {"now": int(now.timestamp()), "aircraft": aircraft}


@router.get("/flights/{flight_id}/track.json")
def flight_track(flight_id: int, db: Session = Depends(get_db), user: Pilot = Depends(require_approved)):
    flight = db.get(Flight, flight_id)
    if flight is None or flight.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Diesen Flug gibt es nicht.")
    points = tracks.points_of(db, flight.id)
    return {**_flight_info(flight), "landed": flight.landing_time is not None, "points": _rows(points)}


def has_track(db: Session, flight_id: int) -> bool:
    return db.get(FlightTrack, flight_id) is not None or db.scalar(
        select(TrackPoint.id).where(TrackPoint.flight_id == flight_id).limit(1)) is not None
