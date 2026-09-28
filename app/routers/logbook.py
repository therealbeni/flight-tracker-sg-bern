from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, Pilot

from templating import templates

router = APIRouter()


@router.get("/logbook")
def logbook(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    flights = db.scalars(
        select(Flight).where(Flight.pilot_id == pilot.id).order_by(Flight.takeoff_time.desc())
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
