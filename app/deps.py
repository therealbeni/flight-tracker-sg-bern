from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db
from models import Pilot, PilotStatus


def get_current_pilot(request: Request, db: Session = Depends(get_db)) -> Pilot | None:
    pilot_id = request.session.get("pilot_id")
    pilot = db.get(Pilot, pilot_id) if pilot_id is not None else None
    request.state.user = pilot  # for the navigation in base.html (templating.current_user)
    return pilot


def require_login(pilot: Pilot | None = Depends(get_current_pilot)) -> Pilot:
    if pilot is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return pilot


def require_approved(pilot: Pilot = Depends(require_login)) -> Pilot:
    if pilot.status != PilotStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dein Konto ist noch nicht freigeschaltet.")
    return pilot


def require_flying_member(pilot: Pilot = Depends(require_approved)) -> Pilot:
    """A person who flies - not the Flugdienstleiter's account."""
    if pilot.is_fdl:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Mit dem Flugdienstleiter-Konto kann man keine Flugzeuge einchecken. "
                                   "Melde dich dafür mit deinem eigenen Konto an.")
    return pilot


def require_admin(pilot: Pilot = Depends(require_approved)) -> Pilot:
    if not pilot.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dafür braucht es Admin-Rechte.")
    return pilot


def back_url(request: Request, default: str) -> str:
    """The page a form was submitted from, if it's one of ours - so a button
    can return the pilot to where they were. Never redirects off-site."""
    referer = request.headers.get("referer") or ""
    base = str(request.base_url)
    return "/" + referer[len(base):] if referer.startswith(base) else default


def local_path(value: str) -> str | None:
    """`value` if it's a path on this site (e.g. a form's "next" field), else None."""
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else None
