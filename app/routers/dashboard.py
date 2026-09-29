from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, GliderClaim, Pilot
from timeutil import local_day_bounds, today_local

from templating import templates

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    now = datetime.now(timezone.utc)
    start, end = local_day_bounds(today_local())
    todays_flights = db.scalars(
        select(Flight)
        .where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
        .order_by(Flight.takeoff_time.desc())
    ).all()

    my_active_claim = db.scalar(
        select(GliderClaim)
        .where(GliderClaim.pilot_id == pilot.id, GliderClaim.consumed_at.is_(None))
        .where(GliderClaim.expires_at > now)
        .order_by(GliderClaim.claimed_at.desc())
    )

    return templates.TemplateResponse(
        request,
        "dashboard/today.html",
        {
            "pilot": pilot,
            "today": today_local(),
            "flights": todays_flights,
            "in_flight_count": sum(1 for f in todays_flights if f.landing_time is None),
            "my_flight_count": sum(1 for f in todays_flights if f.pilot_id == pilot.id),
            "my_active_claim": my_active_claim,
        },
    )
