from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from models import Pilot, PilotStatus


def get_current_pilot(request: Request, db: Session = Depends(get_db)) -> Pilot | None:
    pilot_id = request.session.get("pilot_id")
    if pilot_id is None:
        return None
    return db.get(Pilot, pilot_id)


def require_login(pilot: Pilot | None = Depends(get_current_pilot)) -> Pilot:
    if pilot is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return pilot


def require_approved(pilot: Pilot = Depends(require_login)) -> Pilot:
    if pilot.status != PilotStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dein Konto ist noch nicht freigeschaltet.")
    return pilot


def require_admin(pilot: Pilot = Depends(require_approved)) -> Pilot:
    if not pilot.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dafür braucht es Admin-Rechte.")
    return pilot
