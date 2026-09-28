from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import require_approved
from models import Flight, GliderClaim, Pilot

from templating import templates

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    now = datetime.now(timezone.utc)
    today = now.date()
    flights = db.scalars(select(Flight).order_by(Flight.takeoff_time.desc())).all()
    todays_flights = [f for f in flights if f.takeoff_time and f.takeoff_time.date() == today]

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
            "flights": todays_flights,
            "in_flight_count": sum(1 for f in todays_flights if f.landing_time is None),
            "my_flight_count": sum(1 for f in todays_flights if f.pilot_id == pilot.id),
            "my_active_claim": my_active_claim,
        },
    )
