import io
import uuid
from datetime import date, datetime, timezone

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from deps import require_admin
from models import AircraftKind, Airfield, Flight, Glider, Pilot, PilotRole, PilotStatus
from timeutil import local_day_bounds, to_local

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
    request.session["pilot_id"] = target.id
    request.session["viewing_as_name"] = target.full_name
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/pilots/{pilot_id}/reject")
def reject_pilot(request: Request, pilot_id: int, db: Session = Depends(get_db)):
    pilot = db.get(Pilot, pilot_id)
    if pilot is not None and pilot.id != request.session.get("pilot_id"):  # never lock yourself out
        pilot.status = PilotStatus.REJECTED
        db.commit()
    return RedirectResponse("/admin/pilots", status_code=303)


@router.get("/gliders")
def list_gliders(request: Request, db: Session = Depends(get_db)):
    gliders = db.scalars(select(Glider).order_by(Glider.registration)).all()
    return templates.TemplateResponse(
        request, "admin/gliders.html", {"gliders": gliders, "kinds": list(AircraftKind)}
    )


@router.post("/gliders")
def create_glider(
    request: Request,
    registration: str = Form(...),
    model: str = Form(""),
    ogn_device_id: str = Form(""),
    kind: AircraftKind = Form(AircraftKind.GLIDER),
    db: Session = Depends(get_db),
):
    glider = Glider(
        registration=registration.strip().upper(),
        model=model.strip() or None,
        ogn_device_id=ogn_device_id.strip().upper() or None,
        kind=kind,
        claim_token=str(uuid.uuid4()),
    )
    db.add(glider)
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
def toggle_glider_active(glider_id: int, db: Session = Depends(get_db)):
    glider = db.get(Glider, glider_id)
    if glider is not None:
        glider.active = not glider.active
        db.commit()
    return RedirectResponse("/admin/gliders", status_code=303)


@router.get("/airfields")
def list_airfields(request: Request, db: Session = Depends(get_db)):
    airfields = db.scalars(select(Airfield).order_by(Airfield.icao)).all()
    return templates.TemplateResponse(request, "admin/airfields.html", {"airfields": airfields})


@router.post("/airfields")
def create_airfield(
    icao: str = Form(...),
    name: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    elevation_m: float = Form(0.0),
    db: Session = Depends(get_db),
):
    icao = icao.strip().upper()
    existing = db.get(Airfield, icao)
    if existing is not None:
        existing.name, existing.latitude, existing.longitude, existing.elevation_m = (
            name.strip(),
            latitude,
            longitude,
            elevation_m,
        )
    else:
        db.add(Airfield(icao=icao, name=name.strip(), latitude=latitude, longitude=longitude, elevation_m=elevation_m))
    db.commit()
    return RedirectResponse("/admin/airfields", status_code=303)


@router.get("/finalize")
def finalize_overview(request: Request, db: Session = Depends(get_db)):
    flights = db.scalars(
        select(Flight).where(Flight.takeoff_time.isnot(None), Flight.deleted_at.is_(None))
        .order_by(Flight.takeoff_time.desc())
    ).all()

    by_date: dict[date, list[Flight]] = {}
    for f in flights:
        by_date.setdefault(to_local(f.takeoff_time).date(), []).append(f)

    days = [
        {
            "date": day,
            "total": len(day_flights),
            "verified": sum(1 for f in day_flights if f.verified_by_pilot),
            "landed": sum(1 for f in day_flights if f.landing_time is not None),
            "finalized": all(f.finalized_at is not None for f in day_flights),
        }
        for day, day_flights in sorted(by_date.items(), reverse=True)
    ]
    return templates.TemplateResponse(request, "admin/finalize.html", {"days": days})


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
    return RedirectResponse("/admin/finalize", status_code=303)


@router.post("/finalize/{day}/unlock")
def unlock_day(day: date, db: Session = Depends(get_db)):
    start, end = local_day_bounds(day)
    flights = db.scalars(
        select(Flight).where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
    ).all()
    for f in flights:
        f.finalized_at = None
    db.commit()
    return RedirectResponse("/admin/finalize", status_code=303)
