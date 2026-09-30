"""Flugbuch: all flights of a day, modelled on Vereinsflieger's
Flugdatenerfassung (entry form on top, the day's flights below; click a flight
to load it into the form). Runs on the club PC at the launch point.

Auschecken (checkout): at the end of the day a pilot goes through their
flights, confirms them, and their check-ins end.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import flight_form
from database import get_db
from deps import local_path, require_approved
from flight_form import FlightInput, can_edit, form_choices, read_form
from models import Flight, Pilot
from routers.claim import active_claims
from templating import templates
from timeutil import local_day_bounds, to_local, today_local

router = APIRouter()


def flights_of_day(db: Session, day: date, pilot_id: Optional[int] = None) -> list[Flight]:
    start, end = local_day_bounds(day)
    query = (select(Flight).where(Flight.deleted_at.is_(None))
             .where(Flight.takeoff_time >= start, Flight.takeoff_time < end))
    if pilot_id is not None:
        query = query.where(or_(Flight.pilot_id == pilot_id, Flight.companion_id == pilot_id))
    return list(db.scalars(query.order_by(Flight.takeoff_time)).all())


def render_flugbuch(request: Request, db: Session, user: Pilot, day: date, form: Optional[FlightInput] = None,
                    editing: Optional[Flight] = None, status_code: int = 200):
    flights = flights_of_day(db, day)
    if form is None:
        form = FlightInput.from_flight(db, editing, user) if editing else FlightInput.new(db, user, day)
    pilots_today = sorted({f.pilot for f in flights if f.pilot} | {f.companion for f in flights if f.companion},
                          key=lambda p: p.full_name)
    return templates.TemplateResponse(request, "flugbuch/day.html", {
        "pilot": user, "day": day, "today": today_local(), "flights": flights, "form": form, "editing": editing,
        "can_edit": {f.id: can_edit(f, user) for f in flights}, "pilots_today": pilots_today,
        "day_finalized": bool(flights) and all(f.finalized_at for f in flights if f.landing_time),
        "total_minutes": sum(f.duration_min or 0 for f in flights),
        "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1), "choices": form_choices(db),
    }, status_code=status_code)


@router.get("/flugbuch")
def flugbuch(request: Request, datum: Optional[date] = Query(None), bearbeiten: Optional[int] = Query(None),
             db: Session = Depends(get_db), user: Pilot = Depends(require_approved)):
    day = datum or today_local()
    editing = None
    if bearbeiten is not None:
        editing = db.get(Flight, bearbeiten)
        if editing is None or editing.deleted_at is not None or not can_edit(editing, user):
            editing = None
    return render_flugbuch(request, db, user, day, editing=editing)


@router.post("/flugbuch")
async def flugbuch_add(request: Request, day: date = Form(...), db: Session = Depends(get_db),
                       user: Pilot = Depends(require_approved)):
    """Flug hinzufügen: a flight the tracker couldn't see (no FLARM, out of
    coverage, visiting aircraft)."""
    form = await read_form(request)
    flight = flight_form.save(db, user, form)
    if flight is None:
        db.rollback()
        return render_flugbuch(request, db, user, day, form=form, status_code=400)
    flight.verified_by_pilot = True
    db.commit()
    flown_on = to_local(flight.takeoff_time).date()  # the date field may have moved it
    return RedirectResponse(f"/flugbuch?datum={flown_on}", status_code=303)


def _checkout_target(db: Session, user: Pilot, pilot_id: int) -> Pilot:
    target = db.get(Pilot, pilot_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Diesen Piloten gibt es nicht.")
    if target.id != user.id and not user.edits_all_flights:
        raise HTTPException(status_code=403, detail="Du kannst nur dich selbst auschecken.")
    return target


@router.get("/auschecken/{pilot_id}")
def checkout(request: Request, pilot_id: int, datum: Optional[date] = Query(None), db: Session = Depends(get_db),
             user: Pilot = Depends(require_approved)):
    target = _checkout_target(db, user, pilot_id)
    day = datum or today_local()
    return templates.TemplateResponse(request, "flugbuch/checkout.html", {
        "pilot": user, "target": target, "day": day, "flights": flights_of_day(db, day, target.id),
        "claims": active_claims(db, pilot_id=target.id),
    })


@router.post("/auschecken/{pilot_id}")
def checkout_confirm(pilot_id: int, day: date = Form(...), next: str = Form(""), db: Session = Depends(get_db),
                     user: Pilot = Depends(require_approved)):
    """Confirms the pilot's landed flights of the day and ends their check-ins."""
    target = _checkout_target(db, user, pilot_id)
    for flight in flights_of_day(db, day, target.id):
        if flight.landing_time is not None and flight.finalized_at is None:
            flight.verified_by_pilot = True
    now = datetime.now(timezone.utc)
    for claim in active_claims(db, pilot_id=target.id):
        claim.cancelled_at = now
    db.commit()
    return RedirectResponse(local_path(next) or f"/flugbuch?datum={day}&ausgecheckt={target.id}", status_code=303)
