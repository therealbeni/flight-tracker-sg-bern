"""Heute: the start page. Pilots see their check-ins and the day's flights;
the Flugdienstleiter (FDL) sees the whole day at a glance (day_board.py)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import get_db
from day_board import aircraft_of_day, flights_of_day, issues, pilots_of_day
from deps import require_approved
from models import GliderClaim, Pilot
from routers.claim import active_claims
from templating import templates
from timeutil import as_utc, local_day_bounds, today_local

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
        "ended_by_others": ended_by_others(db, pilot),
    })


def ended_by_others(db: Session, pilot: Pilot) -> list[dict]:
    """Today's check-ins of `pilot` that someone else ended (took the aircraft
    over, or the FDL released it) - until the pilot checks in again (then
    they've moved on). Latest first."""
    start, _ = local_day_bounds(today_local())
    mine = db.scalars(select(GliderClaim).where(GliderClaim.pilot_id == pilot.id, GliderClaim.claimed_at >= start)
                      .options(selectinload(GliderClaim.glider), selectinload(GliderClaim.cancelled_by))).all()
    last_check_in = max((as_utc(c.claimed_at) for c in mine), default=None)
    notices = []
    for claim in sorted(mine, key=lambda c: c.cancelled_at or c.claimed_at, reverse=True):
        if claim.cancelled_by_id in (None, pilot.id) or as_utc(claim.cancelled_at) < last_check_in:
            continue
        took_over = db.scalar(select(GliderClaim.id).where(
            GliderClaim.glider_id == claim.glider_id, GliderClaim.pilot_id == claim.cancelled_by_id,
            GliderClaim.claimed_at == claim.cancelled_at)) is not None
        notices.append({"claim": claim, "took_over": took_over})
    return notices


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
        "issues": (found := issues(flights)),
        "to_check": [f for f in flights if f.id in found],
        "minutes_since": lambda dt: (now - as_utc(dt)).total_seconds() / 60,
    })
