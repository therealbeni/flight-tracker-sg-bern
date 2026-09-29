from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, Pilot, PilotRole
from routers.claim import active_claims
from timeutil import local_day_bounds, today_local

from templating import templates

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    if pilot.role is PilotRole.FLIGHTDESK:
        return RedirectResponse("/flugbuch", status_code=303)  # the club PC works in the Flugbuch
    start, end = local_day_bounds(today_local())
    todays_flights = db.scalars(
        select(Flight)
        .where(Flight.takeoff_time >= start, Flight.takeoff_time < end, Flight.deleted_at.is_(None))
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
            "my_flight_count": sum(1 for f in todays_flights if pilot.id in (f.pilot_id, f.companion_id)),
            "my_claims": active_claims(db, pilot_id=pilot.id),
        },
    )
