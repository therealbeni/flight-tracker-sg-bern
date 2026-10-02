"""Checking in on an aircraft before flying (a "claim"), and releasing it.

The tracker gives a takeoff the pilot of the most recent active claim on that
aircraft - see GliderClaim in shared/models.py.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from database import get_db
from deps import back_url, require_approved
from models import Glider, GliderClaim, Pilot
from templating import templates
from timeutil import local_end_of_day

router = APIRouter()


def active_claims(db: Session, **filters) -> list[GliderClaim]:
    """Active claims, newest first, e.g. active_claims(db, glider_id=3)."""
    query = select(GliderClaim).where(GliderClaim.active_at(datetime.now(timezone.utc)))
    for column, value in filters.items():
        query = query.where(getattr(GliderClaim, column) == value)
    return list(db.scalars(query.order_by(GliderClaim.claimed_at.desc())).all())


def _glider_for_token(db: Session, token: str) -> Glider:
    glider = db.scalar(select(Glider).where(Glider.claim_token == token))
    if glider is None or not glider.active:
        raise HTTPException(status_code=404, detail="Diesen QR-Code kennen wir nicht, oder das Flugzeug ist nicht aktiv.")
    return glider


@router.get("/claim")
def claim_picker(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    gliders = db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.kind, Glider.registration)).all()
    rows = [{"glider": g, "claims": active_claims(db, glider_id=g.id)} for g in gliders]
    return templates.TemplateResponse(request, "claim/picker.html", {
        "rows": rows, "pilot": pilot, "my_claims": active_claims(db, pilot_id=pilot.id)})


@router.get("/claim/{token}")
def claim_form(request: Request, token: str, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    glider = _glider_for_token(db, token)
    return templates.TemplateResponse(request, "claim/claim.html", {
        "glider": glider, "token": token, "pilot": pilot, "claims": active_claims(db, glider_id=glider.id)})


@router.post("/claim/{token}")
def claim_submit(request: Request, token: str, mode: str = Form("next"),
                 db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    glider = _glider_for_token(db, token)
    whole_day = mode == "day"
    now = datetime.now(timezone.utc)

    # A new check-in replaces earlier ones of the same kind on this aircraft
    # (change of plan). A one-flight check-in leaves a whole-day claim alone:
    # the tow pilot's day claim applies again after that one flight.
    superseded = update(GliderClaim).where(GliderClaim.glider_id == glider.id, GliderClaim.active_at(now))
    if not whole_day:
        superseded = superseded.where(GliderClaim.whole_day.is_(False))
    db.execute(superseded.values(cancelled_at=now))

    # Both kinds last until local midnight: waiting hours for a launch is
    # normal. A one-flight check-in ends earlier at its takeoff, when released,
    # or at checkout.
    db.add(GliderClaim(glider_id=glider.id, pilot_id=pilot.id, claimed_at=now, expires_at=local_end_of_day(now),
                       whole_day=whole_day))
    db.commit()
    return templates.TemplateResponse(request, "claim/claim_success.html", {"glider": glider, "whole_day": whole_day})


@router.post("/claims/{claim_id}/release")
def claim_release(request: Request, claim_id: int, db: Session = Depends(get_db),
                  pilot: Pilot = Depends(require_approved)):
    """Freigeben: undo a check-in made by mistake (own claims; admins: any)."""
    claim = db.get(GliderClaim, claim_id)
    if claim is None or (claim.pilot_id != pilot.id and not pilot.is_admin):
        raise HTTPException(status_code=404, detail="Diesen Check-in gibt es nicht.")
    if claim.cancelled_at is None and claim.consumed_at is None:
        claim.cancelled_at = datetime.now(timezone.utc)
        db.commit()
    return RedirectResponse(back_url(request, "/claim"), status_code=303)

