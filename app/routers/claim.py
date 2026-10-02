"""Checking in on an aircraft before flying (a "claim"), and releasing it.

The tracker gives a takeoff the pilot of the most recent active claim on that
aircraft - see GliderClaim in shared/models.py.

Rules when checking in:
  - Someone else is checked in on the aircraft: taking over needs a
    confirmation (form field `takeover`); they see who took it over.
  - A one-flight check-in replaces earlier one-flight check-ins on the
    aircraft but leaves a whole-day one alone (the tow pilot's day claim
    applies again after that one flight). A whole-day check-in replaces all.
  - One pilot flies one aircraft at a time: a one-flight check-in ends the
    pilot's one-flight check-ins on other aircraft.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from deps import back_url, require_approved, require_flying_member
from models import Flight, Glider, GliderClaim, Pilot
from templating import templates
from timeutil import local_day_bounds, local_end_of_day, today_local

router = APIRouter()


def active_claims(db: Session, **filters) -> list[GliderClaim]:
    """Active claims, newest first, e.g. active_claims(db, glider_id=3)."""
    query = select(GliderClaim).where(GliderClaim.active_at(datetime.now(timezone.utc)))
    for column, value in filters.items():
        query = query.where(getattr(GliderClaim, column) == value)
    return list(db.scalars(query.order_by(GliderClaim.claimed_at.desc())).all())


def end_claims(claims, by: Pilot, now: datetime) -> None:
    for claim in claims:
        claim.cancelled_at, claim.cancelled_by_id = now, by.id


def _glider_for_token(db: Session, token: str, lock: bool = False) -> Glider:
    query = select(Glider).where(Glider.claim_token == token)
    if lock:
        # Two pilots checking in on the same aircraft at the same moment:
        # the second waits here until the first is saved, then sees it.
        query = query.with_for_update()
    glider = db.scalar(query)
    if glider is None or not glider.active:
        raise HTTPException(status_code=404, detail="Diesen QR-Code kennen wir nicht, oder das Flugzeug ist nicht aktiv.")
    return glider


def _replaced(claims: list[GliderClaim], whole_day: bool) -> list[GliderClaim]:
    """The aircraft's check-ins a new one replaces (see the module docstring)."""
    return claims if whole_day else [c for c in claims if not c.whole_day]


def render_claim_form(request: Request, db: Session, glider: Glider, token: str, pilot: Pilot,
                      mode: str = "", status_code: int = 200):
    claims = active_claims(db, glider_id=glider.id)
    # In the air: an open flight of today (or of the last hours, just after midnight).
    since = min(local_day_bounds(today_local())[0], datetime.now(timezone.utc) - timedelta(hours=12))
    airborne = db.scalar(select(Flight).where(Flight.glider_id == glider.id, Flight.landing_time.is_(None),
                                              Flight.deleted_at.is_(None), Flight.takeoff_time >= since)
                         .order_by(Flight.takeoff_time.desc()))
    return templates.TemplateResponse(request, "claim/claim.html", {
        "glider": glider, "token": token, "pilot": pilot, "claims": claims, "airborne": airborne, "mode": mode,
        "others": [c for c in claims if c.pilot_id != pilot.id],
    }, status_code=status_code)


@router.get("/claim")
def claim_picker(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_flying_member)):
    gliders = db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.kind, Glider.registration)).all()
    rows = [{"glider": g, "claims": active_claims(db, glider_id=g.id)} for g in gliders]
    return templates.TemplateResponse(request, "claim/picker.html", {
        "rows": rows, "pilot": pilot, "my_claims": active_claims(db, pilot_id=pilot.id)})


@router.get("/claim/{token}")
def claim_form(request: Request, token: str, db: Session = Depends(get_db),
               pilot: Pilot = Depends(require_flying_member)):
    return render_claim_form(request, db, _glider_for_token(db, token), token, pilot)


@router.post("/claim/{token}")
def claim_submit(request: Request, token: str, mode: str = Form("next"), takeover: str = Form(""),
                 db: Session = Depends(get_db), pilot: Pilot = Depends(require_flying_member)):
    glider = _glider_for_token(db, token, lock=True)
    whole_day = mode == "day"
    now = datetime.now(timezone.utc)

    replaced = _replaced(active_claims(db, glider_id=glider.id), whole_day)
    if not takeover and any(c.pilot_id != pilot.id for c in replaced):
        db.rollback()  # releases the lock
        return render_claim_form(request, db, glider, token, pilot, mode=mode, status_code=409)
    end_claims(replaced, pilot, now)

    ended_elsewhere = []
    if not whole_day:
        ended_elsewhere = [c for c in active_claims(db, pilot_id=pilot.id)
                           if c.glider_id != glider.id and not c.whole_day]
        end_claims(ended_elsewhere, pilot, now)

    # Both kinds last until local midnight: waiting hours for a launch is
    # normal. A one-flight check-in ends earlier at its takeoff, when released,
    # or at checkout.
    db.add(GliderClaim(glider_id=glider.id, pilot_id=pilot.id, claimed_at=now, expires_at=local_end_of_day(now),
                       whole_day=whole_day))
    db.commit()
    return templates.TemplateResponse(request, "claim/claim_success.html", {
        "glider": glider, "whole_day": whole_day, "ended_elsewhere": ended_elsewhere})


@router.post("/claims/{claim_id}/release")
def claim_release(request: Request, claim_id: int, db: Session = Depends(get_db),
                  pilot: Pilot = Depends(require_approved)):
    """Freigeben: undo a check-in made by mistake (own claims; FDL and admins: any)."""
    claim = db.get(GliderClaim, claim_id)
    if claim is None or (claim.pilot_id != pilot.id and not pilot.edits_all_flights):
        raise HTTPException(status_code=404, detail="Diesen Check-in gibt es nicht.")
    if claim.cancelled_at is None and claim.consumed_at is None:
        end_claims([claim], pilot, datetime.now(timezone.utc))
        db.commit()
    return RedirectResponse(back_url(request, "/claim"), status_code=303)
