from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, Pilot

from templating import templates

router = APIRouter()


@router.get("/logbook")
def logbook(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    if pilot.is_fdl:
        return RedirectResponse("/dashboard", status_code=303)  # the FDL account has no flights of its own
    flights = db.scalars(
        select(Flight)
        .where(or_(Flight.pilot_id == pilot.id, Flight.companion_id == pilot.id), Flight.deleted_at.is_(None))
        .order_by(Flight.takeoff_time.desc())
    ).all()
    total_minutes = sum(f.duration_min or 0 for f in flights)
    return templates.TemplateResponse(
        request,
        "logbook/logbook.html",
        {
            "pilot": pilot,
            "flights": flights,
            "total_flights": len(flights),
            "total_hours": round(total_minutes / 60, 1),
        },
    )
