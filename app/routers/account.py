"""Konto: everyone's own settings - name, e-mail address, password - and the
private aircraft they own (with the QR code to stick on them).

Changing the e-mail address or the password asks for the current password,
so nobody can take over an account left logged in somewhere. A new password
ends all other sessions (deps.session_stamp) but keeps this one.
"""

import io

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from deps import require_approved, start_session
from models import Glider, Pilot
from routers.auth import EMAIL
from security import hash_password, password_problem, verify_password
from templating import templates

router = APIRouter()

SAVED = {"profil": "Name und E-Mail gespeichert.", "passwort": "Neues Passwort gespeichert. Auf anderen Geräten "
         "musst du dich neu anmelden."}


def owned_aircraft(db: Session, pilot: Pilot) -> list[Glider]:
    return [g for g in db.scalars(select(Glider).order_by(Glider.registration)).all() if g.owned_by(pilot)]


def render_account(request: Request, db: Session, pilot: Pilot, saved: str = "", profile_error: str = "",
                   password_error: str = "", form: dict | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, "account/account.html", {
        "pilot": pilot, "saved": SAVED.get(saved, ""), "profile_error": profile_error,
        "password_error": password_error, "aircraft": owned_aircraft(db, pilot),
        "form": form or {"full_name": pilot.full_name, "email": pilot.email},
    }, status_code=status_code)


@router.get("/konto")
def account(request: Request, gespeichert: str = Query(""), db: Session = Depends(get_db),
            pilot: Pilot = Depends(require_approved)):
    return render_account(request, db, pilot, saved=gespeichert)


@router.post("/konto/profil")
def save_profile(request: Request, full_name: str = Form(""), email: str = Form(""),
                 current_password: str = Form(""), db: Session = Depends(get_db),
                 pilot: Pilot = Depends(require_approved)):
    full_name = " ".join(full_name.split())
    email = email.strip().lower()
    form = {"full_name": full_name, "email": email}
    error = ""
    if not full_name or len(full_name) > 100:
        error = "Bitte gib deinen Vor- und Nachnamen ein (höchstens 100 Zeichen)."
    elif not EMAIL.fullmatch(email) or len(email) > 254:
        error = "Bitte gib eine gültige E-Mail-Adresse ein."
    elif email != pilot.email and not verify_password(current_password, pilot.password_hash):
        error = "Um die E-Mail-Adresse zu ändern, gib dein aktuelles Passwort ein."
    elif email != pilot.email and db.scalar(select(Pilot.id).where(Pilot.email == email)) is not None:
        error = "Mit dieser E-Mail-Adresse gibt es schon ein Konto."
    if error:
        return render_account(request, db, pilot, profile_error=error, form=form, status_code=400)
    pilot.full_name, pilot.email = full_name, email
    try:
        db.commit()
    except IntegrityError:  # the same address taken at the same moment
        db.rollback()
        return render_account(request, db, pilot, profile_error="Mit dieser E-Mail-Adresse gibt es schon ein Konto.",
                              form=form, status_code=400)
    return RedirectResponse("/konto?gespeichert=profil", status_code=303)


@router.post("/konto/passwort")
def change_password(request: Request, current_password: str = Form(""), new_password: str = Form(""),
                    new_password_again: str = Form(""), db: Session = Depends(get_db),
                    pilot: Pilot = Depends(require_approved)):
    error = ""
    if not verify_password(current_password, pilot.password_hash):
        error = "Das aktuelle Passwort stimmt nicht."
    elif new_password != new_password_again:
        error = "Die beiden neuen Passwörter sind nicht gleich."
    else:
        error = password_problem(new_password) or ""
    if error:
        return render_account(request, db, pilot, password_error=error, status_code=400)
    pilot.password_hash = hash_password(new_password)
    db.commit()
    start_session(request, pilot)  # this browser stays logged in, all others are logged out
    return RedirectResponse("/konto?gespeichert=passwort", status_code=303)


@router.get("/konto/flugzeuge/{glider_id}/qr.png")
def own_aircraft_qr(glider_id: int, db: Session = Depends(get_db), pilot: Pilot = Depends(require_approved)):
    """The check-in QR code of an aircraft the pilot owns (admins: see Verwaltung)."""
    glider = db.get(Glider, glider_id)
    if glider is None or not glider.owned_by(pilot):
        raise HTTPException(status_code=404, detail="Dieses Flugzeug gibt es nicht.")
    buf = io.BytesIO()
    qrcode.make(f"{settings.base_url}/claim/{glider.claim_token}").save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")
