import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from day_board import month_calendar, parse_month
from deps import require_admin, start_session
from flight_form import members
from models import AircraftKind, Airfield, Flight, Glider, Pilot, PilotRole, PilotStatus
from routers.claim import active_claims, end_claims
from timeutil import local_day_bounds, to_local, today_local

from templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

ROLE_LABELS = {PilotRole.PILOT: "Pilot", PilotRole.ADMIN: "Admin", PilotRole.FDL: "Flugdienstleiter (FDL)"}


@router.get("/pilots")
def list_pilots(request: Request, db: Session = Depends(get_db)):
    pilots = db.scalars(select(Pilot).order_by(Pilot.status, Pilot.full_name)).all()
    return templates.TemplateResponse(request, "admin/pilots.html", {"pilots": pilots, "roles": ROLE_LABELS})


@router.post("/pilots/{pilot_id}/approve")
def approve_pilot(pilot_id: int, db: Session = Depends(get_db)):
    pilot = db.get(Pilot, pilot_id)
    if pilot is not None:
        pilot.status = PilotStatus.APPROVED
        db.commit()
    return RedirectResponse("/admin/pilots", status_code=303)


@router.post("/pilots/{pilot_id}/role")
def set_pilot_role(request: Request, pilot_id: int, role: PilotRole = Form(...), db: Session = Depends(get_db)):
    """Pilot, Admin, or Flugdienstleiter (the account of the day's flight operations)."""
    pilot = db.get(Pilot, pilot_id)
    if pilot is not None and pilot.id != request.session.get("pilot_id"):  # never lock yourself out
        pilot.role = role
        db.commit()
    return RedirectResponse("/admin/pilots", status_code=303)


@router.post("/pilots/{pilot_id}/view-as")
def view_as_pilot(request: Request, pilot_id: int, db: Session = Depends(get_db)):
    """See the app exactly as this pilot does (to test, or to help someone).
    The admin's own id is kept in the session so they can switch back."""
    target = db.get(Pilot, pilot_id)
    if target is None or target.status != PilotStatus.APPROVED:
        raise HTTPException(status_code=404, detail="Diesen Piloten gibt es nicht, oder er ist nicht freigeschaltet.")
    request.session["viewing_as_admin_id"] = request.session["pilot_id"]
    request.session["viewing_as_admin_stamp"] = request.session["stamp"]
    start_session(request, target)
    request.session["viewing_as_name"] = target.full_name
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/pilots/{pilot_id}/reject")
def reject_pilot(pilot_id: int, db: Session = Depends(get_db), admin: Pilot = Depends(require_admin)):
    pilot = db.get(Pilot, pilot_id)
    if pilot is not None and pilot.id != admin.id:  # never lock yourself out
        pilot.status = PilotStatus.REJECTED
        end_claims(active_claims(db, pilot_id=pilot.id), admin, datetime.now(timezone.utc))
        db.commit()
    return RedirectResponse("/admin/pilots", status_code=303)


def render_gliders(request: Request, db: Session, error: Optional[str] = None):
    gliders = db.scalars(select(Glider).order_by(Glider.registration)).all()
    return templates.TemplateResponse(request, "admin/gliders.html", {
        "gliders": gliders, "kinds": list(AircraftKind), "members": members(db), "error": error},
        status_code=400 if error else 200)


def aircraft_problem(db: Session, registration: str, model: str, ogn_device_id: str,
                     glider: Optional[Glider] = None) -> Optional[str]:
    """What's wrong with these aircraft fields (German), or None. `glider`:
    the aircraft being edited (its own registration/ID don't clash)."""
    others = select(Glider).where(Glider.id != glider.id) if glider else select(Glider)
    if not registration:
        return "Bitte das Kennzeichen eingeben."
    if len(registration) > 32 or len(model) > 128:
        return "Kennzeichen (höchstens 32 Zeichen) oder Typ (höchstens 128) ist zu lang."
    if ogn_device_id and not re.fullmatch(r"[0-9A-F]{6}", ogn_device_id):
        return "Die FLARM-/OGN-ID hat genau 6 Zeichen: Ziffern und A-F, z.B. 4B4BBA."
    if db.scalar(others.where(Glider.registration == registration)):
        return f"Ein Flugzeug {registration} gibt es schon."
    if ogn_device_id and db.scalar(others.where(Glider.ogn_device_id == ogn_device_id)):
        return f"Die OGN-ID {ogn_device_id} gehört schon einem anderen Flugzeug."
    return None


def _owners(db: Session, ids: list[str]) -> list[Pilot]:
    wanted = {int(i) for i in ids if i.strip().isdigit()}
    return [p for p in members(db) if p.id in wanted]


@router.get("/gliders")
def list_gliders(request: Request, db: Session = Depends(get_db)):
    return render_gliders(request, db)


@router.post("/gliders")
def create_glider(
    request: Request,
    registration: str = Form(""),
    model: str = Form(""),
    ogn_device_id: str = Form(""),
    kind: AircraftKind = Form(AircraftKind.GLIDER),
    owner_id: str = Form(""),
    db: Session = Depends(get_db),
):
    registration, model, ogn_device_id = registration.strip().upper(), model.strip(), ogn_device_id.strip().upper()
    if error := aircraft_problem(db, registration, model, ogn_device_id):
        return render_gliders(request, db, error)
    db.add(Glider(registration=registration, model=model or None, ogn_device_id=ogn_device_id or None, kind=kind,
                  claim_token=str(uuid.uuid4()), owners=_owners(db, [owner_id])))
    db.commit()
    return RedirectResponse("/admin/gliders", status_code=303)


def render_glider_edit(request: Request, db: Session, glider: Glider, error: Optional[str] = None):
    return templates.TemplateResponse(request, "admin/glider_edit.html", {
        "glider": glider, "kinds": list(AircraftKind), "members": members(db), "error": error},
        status_code=400 if error else 200)


@router.get("/gliders/{glider_id}")
def edit_glider(request: Request, glider_id: int, db: Session = Depends(get_db)):
    glider = db.get(Glider, glider_id)
    if glider is None:
        raise HTTPException(status_code=404, detail="Dieses Flugzeug gibt es nicht.")
    return render_glider_edit(request, db, glider)


@router.post("/gliders/{glider_id}")
async def save_glider(request: Request, glider_id: int, db: Session = Depends(get_db)):
    """Kennzeichen, Typ, Art, FLARM-ID and owners (none: a club aircraft)."""
    glider = db.get(Glider, glider_id)
    if glider is None:
        raise HTTPException(status_code=404, detail="Dieses Flugzeug gibt es nicht.")
    data = await request.form()
    registration = str(data.get("registration", "")).strip().upper()
    model = str(data.get("model", "")).strip()
    ogn_device_id = str(data.get("ogn_device_id", "")).strip().upper()
    try:
        kind = AircraftKind(str(data.get("kind", "")))
    except ValueError:
        kind = glider.kind
    if error := aircraft_problem(db, registration, model, ogn_device_id, glider):
        return render_glider_edit(request, db, glider, error)
    glider.registration, glider.model, glider.ogn_device_id, glider.kind = \
        registration, model or None, ogn_device_id or None, kind
    glider.owners = _owners(db, [str(v) for v in data.getlist("owner_ids")] + [str(data.get("new_owner_id", ""))])
    db.commit()
    return RedirectResponse("/admin/gliders", status_code=303)


@router.get("/gliders/{glider_id}/qr.png")
def glider_qr_code(glider_id: int, db: Session = Depends(get_db)):
    glider = db.get(Glider, glider_id)
    if glider is None:
        raise HTTPException(status_code=404, detail="Dieses Flugzeug gibt es nicht.")
    claim_url = f"{settings.base_url}/claim/{glider.claim_token}"
    img = qrcode.make(claim_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.post("/gliders/{glider_id}/kind")
def set_glider_kind(glider_id: int, kind: AircraftKind = Form(...), db: Session = Depends(get_db)):
    glider = db.get(Glider, glider_id)
    if glider is not None:
        glider.kind = kind
        db.commit()
    return RedirectResponse("/admin/gliders", status_code=303)


@router.post("/gliders/{glider_id}/toggle-active")
def toggle_glider_active(glider_id: int, db: Session = Depends(get_db), admin: Pilot = Depends(require_admin)):
    glider = db.get(Glider, glider_id)
    if glider is not None:
        glider.active = not glider.active
        if not glider.active:  # out of service: nobody can be checked in on it
            end_claims(active_claims(db, glider_id=glider.id), admin, datetime.now(timezone.utc))
        db.commit()
    return RedirectResponse("/admin/gliders", status_code=303)


def render_airfields(request: Request, db: Session, error: Optional[str] = None):
    airfields = db.scalars(select(Airfield).order_by(Airfield.icao)).all()
    return templates.TemplateResponse(request, "admin/airfields.html", {"airfields": airfields, "error": error},
                                      status_code=400 if error else 200)


@router.get("/airfields")
def list_airfields(request: Request, db: Session = Depends(get_db)):
    return render_airfields(request, db)


@router.post("/airfields")
def create_airfield(
    request: Request,
    icao: str = Form(...),
    name: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    elevation_m: float = Form(0.0),
    db: Session = Depends(get_db),
):
    icao, name = icao.strip().upper(), name.strip()
    if not icao or not name:
        return render_airfields(request, db, "Bitte Code und Name eingeben.")
    if len(icao) > 16 or len(name) > 255:
        return render_airfields(request, db, "Code (höchstens 16 Zeichen) oder Name ist zu lang.")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return render_airfields(request, db, "Breitengrad (-90 bis 90) oder Längengrad (-180 bis 180) stimmt nicht.")
    existing = db.get(Airfield, icao)
    if existing is not None:
        existing.name, existing.latitude, existing.longitude, existing.elevation_m = name, latitude, longitude, elevation_m
    else:
        db.add(Airfield(icao=icao, name=name, latitude=latitude, longitude=longitude, elevation_m=elevation_m))
    db.commit()
    return RedirectResponse("/admin/airfields", status_code=303)


@router.get("/finalize")
def finalize_overview(request: Request, monat: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """A month calendar of flying days: closed (green) or still open (orange).
    Closing and re-opening happens on the day's Flugbuch, next to its flights."""
    first = parse_month(monat, today_local())
    weeks = month_calendar(db, first)
    next_first = (first + timedelta(days=32)).replace(day=1)

    # Open days of all months, so nothing is forgotten in a month nobody looks at.
    open_days = sorted({to_local(t).date() for t in db.scalars(
        select(Flight.takeoff_time).where(Flight.deleted_at.is_(None), Flight.finalized_at.is_(None),
                                          Flight.takeoff_time.is_not(None))).all()}, reverse=True)
    return templates.TemplateResponse(request, "admin/finalize.html", {
        "month": first, "weeks": weeks, "open_days": open_days, "today": today_local(),
        "prev_month": (first - timedelta(days=1)).replace(day=1), "next_month": next_first,
    })


@router.post("/finalize/{day}")
def finalize_day(day: date, db: Session = Depends(get_db)):
    start, end = local_day_bounds(day)
    flights = db.scalars(
        select(Flight)
        .where(Flight.takeoff_time >= start, Flight.takeoff_time < end, Flight.landing_time.isnot(None))
    ).all()
    now = datetime.now(timezone.utc)
    for f in flights:
        f.finalized_at = now
    db.commit()
    return RedirectResponse(f"/flugbuch?datum={day}", status_code=303)


@router.post("/finalize/{day}/unlock")
def unlock_day(day: date, db: Session = Depends(get_db)):
    start, end = local_day_bounds(day)
    flights = db.scalars(
        select(Flight).where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
    ).all()
    for f in flights:
        f.finalized_at = None
    db.commit()
    return RedirectResponse(f"/flugbuch?datum={day}", status_code=303)
