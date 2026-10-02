"""Heute: the start page. Pilots see their check-ins and the day's flights;
the Flugdienstleiter (FDL) sees the whole day at a glance (day_board.py)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from database import get_db
from day_board import aircraft_of_day, flights_of_day, pilots_of_day
from deps import require_approved
from models import Pilot
from routers.claim import active_claims
from templating import templates
from timeutil import as_utc, today_local

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    today = today_local()
    flights = flights_of_day(db, today)
    if pilot.is_fdl:
        return fdl_dashboard(request, db, pilot, flights)
    me = next((pd for pd in pilots_of_day(db, today, flights) if pd.pilot.id == pilot.id), None)
    return templates.TemplateResponse(request, "dashboard/today.html", {
        "pilot": pilot,
        "today": today,
        "flights": flights[::-1],  # latest first
        "in_flight_count": sum(1 for f in flights if f.landing_time is None),
        "my_flight_count": len(me.flights) if me else 0,
        "my_claims": active_claims(db, pilot_id=pilot.id),
        "checked_out_at": me.checked_out_at if me else None,
    })


def fdl_dashboard(request: Request, db: Session, user: Pilot, flights: list):
    today = today_local()
    now = datetime.now(timezone.utc)
    pilots = pilots_of_day(db, today, flights)
    aircraft = aircraft_of_day(db, today, flights)
    return templates.TemplateResponse(request, "dashboard/fdl.html", {
        "pilot": user,
        "today": today,
        "flights": flights,
        "aircraft": aircraft,
        "pilots": pilots,
        "present": [pd for pd in pilots if pd.status != "out"],
        "gone": [pd for pd in pilots if pd.status == "out"],
        "airborne": [a for a in aircraft if a.airborne],
        "to_check": [f for f in flights if f.needs_attention],
        "minutes_since": lambda dt: (now - as_utc(dt)).total_seconds() / 60,
    })
