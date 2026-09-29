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
from models import Airfield, Flight, Glider, Pilot, PilotStatus
from timeutil import local_day_bounds, to_local

from templating import templates

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/pilots")
def list_pilots(request: Request, db: Session = Depends(get_db)):
    pilots = db.scalars(select(Pilot).order_by(Pilot.status, Pilot.full_name)).all()
    return templates.TemplateResponse(request, "admin/pilots.html", {"pilots": pilots})


@router.post("/pilots/{pilot_id}/approve")
def approve_pilot(pilot_id: int, db: Session = Depends(get_db)):
    pilot = db.get(Pilot, pilot_id)
    if pilot is not None:
        pilot.status = PilotStatus.APPROVED
        db.commit()
    return RedirectResponse("/admin/pilots", status_code=303)


@router.post("/pilots/{pilot_id}/reject")
def reject_pilot(pilot_id: int, db: Session = Depends(get_db)):
    pilot = db.get(Pilot, pilot_id)
    if pilot is not None:
        pilot.status = PilotStatus.REJECTED
        db.commit()
    return RedirectResponse("/admin/pilots", status_code=303)


@router.get("/gliders")
def list_gliders(request: Request, db: Session = Depends(get_db)):
    gliders = db.scalars(select(Glider).order_by(Glider.registration)).all()
    return templates.TemplateResponse(
        request, "admin/gliders.html", {"gliders": gliders, "base_url": request.base_url}
    )


@router.post("/gliders")
def create_glider(
    request: Request,
    registration: str = Form(...),
    model: str = Form(""),
    ogn_device_id: str = Form(""),
    db: Session = Depends(get_db),
):
    glider = Glider(
        registration=registration.strip(),
        model=model.strip() or None,
        ogn_device_id=ogn_device_id.strip().upper() or None,
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
        select(Flight).where(Flight.takeoff_time.isnot(None)).order_by(Flight.takeoff_time.desc())
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
