"""One flight: view, correct, confirm, delete. The rules live in flight_form.py."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import flight_form
from database import get_db
from deps import back_url, local_path, require_approved
from flight_form import FlightInput, can_edit, form_choices, read_form
from models import Flight, FlightAuditEntry, Pilot
from routers.flugbuch import render_flugbuch
from templating import templates
from timeutil import to_local, today_local

router = APIRouter()


def get_flight(db: Session, flight_id: int) -> Flight:
    flight = db.get(Flight, flight_id)
    if flight is None or flight.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Diesen Flug gibt es nicht.")
    return flight


def render_detail(request: Request, db: Session, flight: Flight, user: Pilot, form: FlightInput | None = None,
                  status_code: int = 200):
    history = db.scalars(
        select(FlightAuditEntry).where(FlightAuditEntry.flight_id == flight.id).order_by(FlightAuditEntry.changed_at.desc())
    ).all()
    return templates.TemplateResponse(request, "flights/detail.html", {
        "flight": flight, "pilot": user, "form": form or FlightInput.from_flight(db, flight, user), "history": history,
        "labels": flight_form.FIELD_LABELS, "can_edit": can_edit(flight, user), "choices": form_choices(db),
    }, status_code=status_code)


@router.get("/flights/{flight_id}")
def flight_detail(request: Request, flight_id: int, db: Session = Depends(get_db),
                  user: Pilot = Depends(require_approved)):
    return render_detail(request, db, get_flight(db, flight_id), user)


@router.post("/flights/{flight_id}")
async def flight_update(request: Request, flight_id: int, next: str = Form(""), db: Session = Depends(get_db),
                        user: Pilot = Depends(require_approved)):
    flight = get_flight(db, flight_id)
    if not can_edit(flight, user):
        raise HTTPException(status_code=403, detail="Diesen Flug kannst du nicht ändern.")
    form = await read_form(request)
    day = to_local(flight.takeoff_time).date() if flight.takeoff_time else today_local()
    if flight_form.save(db, user, form, flight) is None:
        db.rollback()
        if next.startswith("/flugbuch"):
            return render_flugbuch(request, db, user, day, form=form, editing=flight, status_code=400)
        return render_detail(request, db, flight, user, form, status_code=400)
    flight.verified_by_pilot = True
    db.commit()
    return RedirectResponse(local_path(next) or f"/flights/{flight.id}", status_code=303)


@router.post("/flights/{flight_id}/verify")
def flight_verify(request: Request, flight_id: int, db: Session = Depends(get_db),
                  user: Pilot = Depends(require_approved)):
    flight = get_flight(db, flight_id)
    if not can_edit(flight, user):
        raise HTTPException(status_code=403, detail="Diesen Flug kannst du nicht ändern.")
    flight.verified_by_pilot = True
    db.commit()
    return RedirectResponse(back_url(request, "/dashboard"), status_code=303)


@router.post("/flights/{flight_id}/delete")
def flight_delete(request: Request, flight_id: int, db: Session = Depends(get_db),
                  user: Pilot = Depends(require_approved)):
    """For false detections and duplicates. FDL and admins only."""
    flight = get_flight(db, flight_id)
    if not (user.edits_all_flights and can_edit(flight, user)):
        raise HTTPException(status_code=403, detail="Flüge löschen kann nur der Flugdienstleiter oder ein Admin.")
    day = to_local(flight.takeoff_time).date() if flight.takeoff_time else today_local()
    flight_form.delete(db, user, flight)
    db.commit()
    return RedirectResponse(f"/flugbuch?datum={day}", status_code=303)
