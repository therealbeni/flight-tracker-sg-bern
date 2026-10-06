"""Statistik: the club's numbers and your own (app/flight_stats.py)."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

import flight_stats as st
from database import get_db
from deps import require_approved
from models import Pilot
from templating import templates
from timeutil import today_local

router = APIRouter()


@router.get("/statistik")
def statistik(request: Request, ansicht: str = Query(""), jahr: str = Query(""), db: Session = Depends(get_db),
              user: Pilot = Depends(require_approved)):
    # The FDL account flies no flights of its own: only the club's numbers.
    view = "verein" if user.is_fdl or ansicht == "verein" else "ich"
    years = st.years_with_flights(db)
    year: Optional[int] = None if jahr == "alle" else (int(jahr) if jahr.isdigit() else today_local().year)
    context = {"pilot": user, "view": view, "year": year, "years": sorted(set(years) | {today_local().year},
                                                                         reverse=True),
               "launch_labels": st.LAUNCH_LABELS}
    if view == "verein":
        stats = st.club_stats(st.flights_in(db, year))
    else:
        stats = st.pilot_stats(st.flights_in(db, year, pilot=user), user)
        # Recency always looks at the last 90 days, whatever year is shown.
        context["recent"] = st.pilot_stats(st.flights_in(db, None, pilot=user), user).recent
        context["recency_launches"] = st.RECENCY_LAUNCHES
        context["recency_days"] = st.RECENCY_DAYS
    context.update(stats=stats, chart=st.month_chart(stats.months),
                   top_minutes=max((r.total.minutes for r in stats.aircraft), default=0))
    return templates.TemplateResponse(request, "statistik/statistik.html", context)
