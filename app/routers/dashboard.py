from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, Pilot
from routers.claim import active_claims
from timeutil import local_day_bounds, today_local

from templating import templates

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    start, end = local_day_bounds(today_local())
    todays_flights = db.scalars(
        select(Flight)
        .where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
        .order_by(Flight.takeoff_time.desc())
    ).all()

    return templates.TemplateResponse(
        request,
        "dashboard/today.html",
        {
            "pilot": pilot,
            "today": today_local(),
            "flights": todays_flights,
            "in_flight_count": sum(1 for f in todays_flights if f.landing_time is None),
            "my_flight_count": sum(1 for f in todays_flights if f.pilot_id == pilot.id),
            "my_claims": active_claims(db, pilot_id=pilot.id),
        },
    )
