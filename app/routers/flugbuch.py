"""Flugbuch: all flights of a day, modelled on Vereinsflieger's
Flugdatenerfassung. On the club PC it is the Flugdienstleiter's (FDL) command
center: the day's flights, refreshing themselves; the flight form opens on
top for adding or correcting a flight; a side panel shows the aircraft
(in the air, checked in, free) and the pilots of the day.

Pilots without their phone check in at the club PC (Einchecken, FDL and
admins), with the same rules as on the phone (routers/claim.py).

Auschecken (checkout): at the end of the day a pilot goes through their
flights, confirms them, their check-ins end, and the FDL sees they've gone
home (see day_board.py).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import flight_form
from database import get_db
from day_board import (aircraft_of_day, finalize_day, flights_of_day, month_calendar, parse_month, pilots_of_day,
                       possible_duplicates, record_checkout)
from deps import local_path, require_approved
from flight_form import FlightInput, can_edit, form_choices, members, read_form
from models import Flight, Glider, GliderClaim, Pilot, PilotStatus
from routers.claim import CheckIn, active_claims, check_in
from templating import templates
from timeutil import as_utc, local_day_bounds, to_local, today_local

router = APIRouter()


def flying_day_before(db: Session, day: date) -> Optional[date]:
    """The last day before `day` with flights (empty days are skipped)."""
    start, _ = local_day_bounds(day)
    latest = db.scalar(select(func.max(Flight.takeoff_time))
                       .where(Flight.deleted_at.is_(None), Flight.takeoff_time < start))
    return to_local(latest).date() if latest else None


def flying_day_after(db: Session, day: date) -> Optional[date]:
    """The next day after `day` with flights; today if there's none in between."""
    _, end = local_day_bounds(day)
    earliest = db.scalar(select(func.min(Flight.takeoff_time))
                         .where(Flight.deleted_at.is_(None), Flight.takeoff_time >= end))
    following = to_local(earliest).date() if earliest else None
    today = today_local()
    if day < today and (following is None or following > today):
        return today
    return following


@dataclass
class CheckInForm:
    """The club PC's Einchecken form, as submitted."""

    pilot_id: str = ""
    glider_id: str = ""
    mode: str = ""  # "next" or "day"; empty: what suits the aircraft
    error: str = ""
    conflicts: list[GliderClaim] = field(default_factory=list)  # asks to confirm taking over


def render_flugbuch(request: Request, db: Session, user: Pilot, day: date, form: Optional[FlightInput] = None,
                    editing: Optional[Flight] = None, open_form: bool = False, month: Optional[date] = None,
                    checkin: Optional[CheckInForm] = None, notice: str = "", status_code: int = 200):
    """The page. `open_form`: the flight form shows (adding a flight, or it
    came back with errors); it always does when `editing`. `month`: the
    calendar is open on that month. `checkin`: the Einchecken form shows."""
    flights = flights_of_day(db, day)
    if form is None:
        form = FlightInput.from_flight(db, editing, user) if editing else FlightInput.new(db, user, day)
    now = datetime.now(timezone.utc)
    pilots = pilots_of_day(db, day, flights)
    is_desk = user.edits_all_flights
    context = {
        "pilot": user, "day": day, "today": today_local(), "flights": flights, "form": form, "editing": editing,
        "open_form": open_form or editing is not None or bool(form.errors),
        "can_edit": {f.id: can_edit(f, user) for f in flights},
        "pilots_today": pilots, "duplicates": possible_duplicates(flights),
        "day_finalized": bool(flights) and all(f.finalized_at for f in flights if f.landing_time),
        "total_minutes": sum(f.duration_min or 0 for f in flights),
        "airborne_count": sum(1 for f in flights if f.landing_time is None),
        "minutes_since": lambda dt: (now - as_utc(dt)).total_seconds() / 60,
        "prev_day": flying_day_before(db, day), "next_day": flying_day_after(db, day), "choices": form_choices(db),
        "calendar_month": month or day.replace(day=1), "calendar_open": month is not None,
        "calendar": month_calendar(db, month or day.replace(day=1)),
        "notice": notice, "desk": is_desk, "checkin": checkin,
    }
    if is_desk:
        # The side panel and the club PC's Einchecken (both about today).
        today = today_local()
        today_flights = flights if day == today else flights_of_day(db, today)
        aircraft = aircraft_of_day(db, today, today_flights)
        board_pilots = pilots if day == today else pilots_of_day(db, today, today_flights)
        context.update({
            "aircraft": aircraft,
            "present": [pd for pd in board_pilots if pd.status != "out"],
            "gone": [pd for pd in board_pilots if pd.status == "out"],
            "checked_in_count": sum(1 for pd in board_pilots if pd.status == "checked_in"),
            "checkin_aircraft": checkin_aircraft(db, aircraft),
            "members": members(db),
        })
    return templates.TemplateResponse(request, "flugbuch/day.html", context, status_code=status_code)


def checkin_aircraft(db: Session, board) -> list[dict]:
    """The aircraft the club PC can check pilots in on, with what they're doing."""
    by_id = {a.glider.id: a for a in board}
    gliders = db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.kind, Glider.registration)).all()
    rows = [{"glider": g, "day": by_id.get(g.id)} for g in gliders]
    return rows


@router.get("/flugbuch")
def flugbuch(request: Request, datum: Optional[date] = Query(None), bearbeiten: Optional[int] = Query(None),
             neu: bool = Query(False), monat: Optional[str] = Query(None),
             eingecheckt: Optional[int] = Query(None), einchecken: Optional[int] = Query(None),
             db: Session = Depends(get_db), user: Pilot = Depends(require_approved)):
    day = datum or today_local()
    editing = None
    if bearbeiten is not None:
        editing = db.get(Flight, bearbeiten)
        if editing is None or editing.deleted_at is not None or not can_edit(editing, user):
            editing = None
    notice = ""
    if eingecheckt is not None and user.edits_all_flights:
        claim = db.get(GliderClaim, eingecheckt)
        if claim is not None:
            notice = (f"{claim.pilot.full_name} ist auf {claim.glider.registration} eingecheckt "
                      f"({'ganzer Tag' if claim.whole_day else 'nächster Start'}).")
    checkin = None
    if einchecken is not None and user.edits_all_flights:
        checkin = CheckInForm(glider_id=str(einchecken))  # "Einchecken" next to an aircraft in the side panel
    month = parse_month(monat, day) if monat else None
    return render_flugbuch(request, db, user, day, editing=editing, open_form=neu, month=month, checkin=checkin,
                           notice=notice)


@router.post("/flugbuch")
async def flugbuch_add(request: Request, day: date = Form(...), db: Session = Depends(get_db),
                       user: Pilot = Depends(require_approved)):
    """Flug hinzufügen: a flight the tracker couldn't see (no FLARM, out of
    coverage, visiting aircraft)."""
    form = await read_form(request)
    flight = flight_form.save(db, user, form)
    if flight is None:
        db.rollback()
        return render_flugbuch(request, db, user, day, form=form, open_form=True, status_code=400)
    flight.verified_by_pilot = True
    db.commit()
    flown_on = to_local(flight.takeoff_time).date()  # the date field may have moved it
    return RedirectResponse(f"/flugbuch?datum={flown_on}", status_code=303)


@router.post("/flugbuch/einchecken")
def desk_check_in(request: Request, pilot_id: str = Form(""), glider_id: str = Form(""), mode: str = Form(""),
                  takeover: str = Form(""), db: Session = Depends(get_db), user: Pilot = Depends(require_approved)):
    """Einchecken at the club PC, for pilots without their phone."""
    if not user.edits_all_flights:
        raise HTTPException(status_code=403, detail="Einchecken für andere kann nur der Flugdienstleiter. "
                                                    "Checke unter «Einchecken» selbst ein.")
    form = CheckInForm(pilot_id=pilot_id, glider_id=glider_id, mode=mode)
    target = db.get(Pilot, int(pilot_id)) if pilot_id.isdigit() else None
    glider = db.scalar(select(Glider).where(Glider.id == int(glider_id)).with_for_update()) \
        if glider_id.isdigit() else None
    if target is None or target.status != PilotStatus.APPROVED or target.is_fdl:
        form.error = "Bitte den Piloten wählen."
    elif glider is None or not glider.active:
        form.error = "Bitte das Flugzeug wählen."
    if form.error:
        db.rollback()
        return render_flugbuch(request, db, user, today_local(), checkin=form, status_code=400)
    whole_day = mode == "day" if mode in ("day", "next") else glider.kind.flies_all_day
    done: CheckIn = check_in(db, glider, target, whole_day, takeover=bool(takeover), by=user)
    if done.claim is None:
        form.conflicts = done.conflicts
        form.mode = "day" if whole_day else "next"
        db.rollback()  # releases the lock
        return render_flugbuch(request, db, user, today_local(), checkin=form, status_code=409)
    db.commit()
    return RedirectResponse(f"/flugbuch?eingecheckt={done.claim.id}", status_code=303)


@router.post("/flugbuch/abschliessen")
def close_day(day: date = Form(...), db: Session = Depends(get_db), user: Pilot = Depends(require_approved)):
    """Tag abschliessen (FDL and admins): the day's landed flights are locked.
    Only an admin can open a day again (Verwaltung)."""
    if not user.edits_all_flights:
        raise HTTPException(status_code=403, detail="Einen Tag abschliessen kann der Flugdienstleiter oder ein Admin.")
    finalize_day(db, day)
    db.commit()
    return RedirectResponse(f"/flugbuch?datum={day}", status_code=303)


def _checkout_target(db: Session, user: Pilot, pilot_id: int) -> Pilot:
    target = db.get(Pilot, pilot_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Diesen Piloten gibt es nicht.")
    if target.id != user.id and not user.edits_all_flights:
        raise HTTPException(status_code=403, detail="Du kannst nur dich selbst auschecken.")
    if target.is_fdl:
        raise HTTPException(status_code=404, detail="Das Flugdienstleiter-Konto checkt nicht aus.")
    return target


@router.get("/auschecken/{pilot_id}")
def checkout(request: Request, pilot_id: int, datum: Optional[date] = Query(None), next: str = Query(""),
             db: Session = Depends(get_db), user: Pilot = Depends(require_approved)):
    target = _checkout_target(db, user, pilot_id)
    day = datum or today_local()
    flights = flights_of_day(db, day, target.id)
    return templates.TemplateResponse(request, "flugbuch/checkout.html", {
        "pilot": user, "target": target, "day": day, "flights": flights,
        "airborne": [f for f in flights if f.landing_time is None],
        "claims": active_claims(db, pilot_id=target.id), "next": local_path(next) or "",
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
    record_checkout(db, target, day, user)
    db.commit()
    return RedirectResponse(local_path(next) or f"/flugbuch?datum={day}&ausgecheckt={target.id}", status_code=303)
