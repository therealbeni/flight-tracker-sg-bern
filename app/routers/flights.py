from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Airfield, Flight, FlightAuditEntry, Pilot, PilotStatus

from templating import templates

router = APIRouter()


def _get_flight_or_404(db: Session, flight_id: int) -> Flight:
    flight = db.get(Flight, flight_id)
    if flight is None:
        raise HTTPException(status_code=404, detail="Unknown flight")
    return flight


def _can_edit(flight: Flight, pilot: Pilot) -> bool:
    if flight.finalized_at is not None:
        return False  # locked for everyone, including admins, until explicitly unlocked
    if pilot.is_admin:
        return True
    return flight.pilot_id is None or flight.pilot_id == pilot.id


def _audit(db: Session, flight: Flight, pilot: Pilot, field: str, old_value, new_value) -> None:
    old_str = str(old_value) if old_value is not None else None
    new_str = str(new_value) if new_value is not None else None
    if old_str == new_str:
        return
    db.add(
        FlightAuditEntry(
            flight_id=flight.id,
            changed_by_pilot_id=pilot.id,
            field_name=field,
            old_value=old_str,
            new_value=new_str,
        )
    )


@router.get("/flights/{flight_id}")
def flight_detail(request: Request, flight_id: int, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    flight = _get_flight_or_404(db, flight_id)
    airfields = db.scalars(select(Airfield).order_by(Airfield.icao)).all()
    pilots = db.scalars(
        select(Pilot).where(Pilot.status == PilotStatus.APPROVED).order_by(Pilot.full_name)
    ).all()
    history = db.scalars(
        select(FlightAuditEntry).where(FlightAuditEntry.flight_id == flight.id).order_by(FlightAuditEntry.changed_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "flights/detail.html",
        {
            "flight": flight,
            "airfields": airfields,
            "pilots": pilots,
            "history": history,
            "can_edit": _can_edit(flight, pilot),
            "pilot": pilot,
        },
    )


@router.post("/flights/{flight_id}")
def flight_update(
    request: Request,
    flight_id: int,
    db: Session = Depends(get_db),
    pilot: Pilot = Depends(require_approved),
    pilot_id: str = Form(""),
    takeoff_airfield_icao: str = Form(""),
    landing_airfield_icao: str = Form(""),
    notes: str = Form(""),
):
    flight = _get_flight_or_404(db, flight_id)
    if not _can_edit(flight, pilot):
        raise HTTPException(status_code=403, detail="This flight can no longer be edited")

    new_pilot_id = int(pilot_id) if pilot_id else None
    # A non-admin can only assign the flight to themselves, not to anyone else.
    if not pilot.is_admin and new_pilot_id is not None and new_pilot_id != pilot.id:
        raise HTTPException(status_code=403, detail="You can only assign a flight to yourself")

    new_takeoff_icao = takeoff_airfield_icao.strip().upper() or None
    new_landing_icao = landing_airfield_icao.strip().upper() or None
    # Both are foreign keys into `airfields` - an unrecognised code would otherwise
    # fail as a raw 500 on commit instead of a message the pilot can act on.
    for icao in (new_takeoff_icao, new_landing_icao):
        if icao is not None and db.get(Airfield, icao) is None:
            airfields = db.scalars(select(Airfield).order_by(Airfield.icao)).all()
            pilots = db.scalars(
                select(Pilot).where(Pilot.status == PilotStatus.APPROVED).order_by(Pilot.full_name)
            ).all()
            history = db.scalars(
                select(FlightAuditEntry).where(FlightAuditEntry.flight_id == flight.id).order_by(FlightAuditEntry.changed_at.desc())
            ).all()
            return templates.TemplateResponse(
                request,
                "flights/detail.html",
                {
                    "flight": flight,
                    "airfields": airfields,
                    "pilots": pilots,
                    "history": history,
                    "can_edit": True,
                    "pilot": pilot,
                    "error": f"'{icao}' isn't a known airfield yet - ask an admin to add it under Admin > Airfields first.",
                },
                status_code=400,
            )

    _audit(db, flight, pilot, "pilot_id", flight.pilot_id, new_pilot_id)
    flight.pilot_id = new_pilot_id

    _audit(db, flight, pilot, "takeoff_airfield_icao", flight.takeoff_airfield_icao, new_takeoff_icao)
    flight.takeoff_airfield_icao = new_takeoff_icao

    _audit(db, flight, pilot, "landing_airfield_icao", flight.landing_airfield_icao, new_landing_icao)
    flight.landing_airfield_icao = new_landing_icao
    if new_landing_icao is not None:
        # A real airfield has now been identified for what may have been a
        # candidate outlanding - the raw coordinates have served their purpose.
        flight.landing_latitude = None
        flight.landing_longitude = None

    _audit(db, flight, pilot, "notes", flight.notes, notes.strip() or None)
    flight.notes = notes.strip() or None

    flight.verified_by_pilot = True
    db.commit()

    return RedirectResponse(f"/flights/{flight.id}", status_code=303)


@router.post("/flights/{flight_id}/verify")
def flight_verify(
    request: Request, flight_id: int, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)
):
    flight = _get_flight_or_404(db, flight_id)
    if not _can_edit(flight, pilot):
        raise HTTPException(status_code=403, detail="This flight can no longer be edited")
    flight.verified_by_pilot = True
    db.commit()
    redirect_to = request.headers.get("referer") or "/dashboard"
    return RedirectResponse(redirect_to, status_code=303)
