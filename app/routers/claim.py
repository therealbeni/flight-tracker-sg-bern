from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from deps import require_approved
from models import Glider, GliderClaim, Pilot

from templating import templates

router = APIRouter()


def _active_claim(db: Session, glider_id: int) -> GliderClaim | None:
    return db.scalar(
        select(GliderClaim)
        .where(GliderClaim.glider_id == glider_id, GliderClaim.consumed_at.is_(None))
        .where(GliderClaim.expires_at > datetime.now(timezone.utc))
        .order_by(GliderClaim.claimed_at.desc())
    )


@router.get("/claim")
def claim_picker(request: Request, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    gliders = db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.registration)).all()
    rows = [{"glider": g, "active_claim": _active_claim(db, g.id)} for g in gliders]
    return templates.TemplateResponse(request, "claim/picker.html", {"rows": rows})


@router.get("/claim/{token}")
def claim_form(request: Request, token: str, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    glider = db.scalar(select(Glider).where(Glider.claim_token == token))
    if glider is None or not glider.active:
        raise HTTPException(status_code=404, detail="Unknown or inactive glider QR code")

    active_claim = _active_claim(db, glider.id)
    return templates.TemplateResponse(
        request,
        "claim/claim.html",
        {"glider": glider, "token": token, "active_claim": active_claim},
    )


@router.post("/claim/{token}")
def claim_submit(
    request: Request, token: str, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)
):
    glider = db.scalar(select(Glider).where(Glider.claim_token == token))
    if glider is None or not glider.active:
        raise HTTPException(status_code=404, detail="Unknown or inactive glider QR code")

    now = datetime.now(timezone.utc)
    # Superseding an existing unconsumed claim (e.g. a change of plan) rather than
    # stacking claims - only the latest one should be matched to the next takeoff.
    db.execute(
        GliderClaim.__table__.update()
        .where(GliderClaim.glider_id == glider.id, GliderClaim.consumed_at.is_(None))
        .values(consumed_at=now)
    )

    claim = GliderClaim(
        glider_id=glider.id,
        pilot_id=pilot.id,
        claimed_at=now,
        expires_at=now + timedelta(minutes=settings.claim_ttl_minutes),
    )
    db.add(claim)
    db.commit()

    return templates.TemplateResponse(request, "claim/claim_success.html", {"glider": glider})
