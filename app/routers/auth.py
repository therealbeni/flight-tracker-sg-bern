import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from deps import local_path, session_stamp, start_session
from email_sender import sender
from models import Pilot, PilotRole, PilotStatus, PasswordResetToken
from security import (client_ip, generate_token, hash_password, hash_token, login_throttle, password_problem,
                      reset_throttle, signup_throttle, verify_password)

from templating import templates

router = APIRouter()

# Something@something.something, no spaces or line breaks (which would let an
# address smuggle extra headers into an e-mail).
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# Compared against when the e-mail is unknown, so a login takes as long for an
# unknown address as for a wrong password (no telling which addresses exist).
_DUMMY_HASH = hash_password("not a real password")


@router.get("/signup")
def signup_form(request: Request):
    return templates.TemplateResponse(request, "auth/signup.html", {"error": None})


@router.post("/signup")
def signup_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    full_name = " ".join(full_name.split())
    ip = client_ip(request)
    if signup_throttle.blocked(f"ip:{ip}"):
        return templates.TemplateResponse(request, "auth/signup.html", {
            "error": "Von hier wurden gerade viele Konten erstellt. Bitte versuche es später nochmals."}, status_code=429)
    signup_throttle.record(f"ip:{ip}")
    error = None
    if not EMAIL.fullmatch(email):
        error = "Bitte gib eine gültige E-Mail-Adresse ein."
    elif db.scalar(select(Pilot).where(Pilot.email == email)):
        error = "Mit dieser E-Mail-Adresse gibt es schon ein Konto."
    elif not full_name or len(full_name) > 100:
        error = "Bitte gib deinen Vor- und Nachnamen ein (höchstens 100 Zeichen)."
    elif len(email) > 254:
        error = "Bitte gib eine gültige E-Mail-Adresse ein."
    else:
        error = password_problem(password)
    if error:
        return templates.TemplateResponse(request, "auth/signup.html", {"error": error}, status_code=400)

    # First-ever account becomes an approved admin so someone can approve everyone else.
    is_first_account = db.scalar(select(Pilot.id).limit(1)) is None
    pilot = Pilot(
        full_name=full_name,
        email=email,
        password_hash=hash_password(password),
        role=PilotRole.ADMIN if is_first_account else PilotRole.PILOT,
        status=PilotStatus.APPROVED if is_first_account else PilotStatus.PENDING,
    )
    db.add(pilot)
    try:
        db.commit()
    except IntegrityError:  # the same signup sent twice at once (double tap)
        db.rollback()
        return templates.TemplateResponse(request, "auth/signup.html", {
            "error": "Mit dieser E-Mail-Adresse gibt es schon ein Konto."}, status_code=400)

    if is_first_account:
        start_session(request, pilot)
        return RedirectResponse("/dashboard", status_code=303)

    return templates.TemplateResponse(request, "auth/pending.html", {})


@router.get("/login")
def login_form(request: Request, next: str = ""):
    return templates.TemplateResponse(request, "auth/login.html", {"error": None, "next": local_path(next) or ""})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    next = local_path(next) or ""
    ip = client_ip(request)
    if login_throttle.blocked(email, ip):
        return templates.TemplateResponse(request, "auth/login.html", {
            "error": "Zu viele Versuche. Warte 15 Minuten, oder setze dein Passwort über «Passwort vergessen?» zurück.",
            "next": next}, status_code=429)
    pilot = db.scalar(select(Pilot).where(Pilot.email == email))
    if not verify_password(password, pilot.password_hash if pilot else _DUMMY_HASH) or pilot is None:
        login_throttle.failed(email, ip)
        return templates.TemplateResponse(
            request, "auth/login.html", {"error": "E-Mail oder Passwort falsch.", "next": next}, status_code=400
        )
    login_throttle.succeeded(email)
    if pilot.status == PilotStatus.PENDING:
        return templates.TemplateResponse(request, "auth/pending.html", {})
    if pilot.status == PilotStatus.REJECTED:
        return templates.TemplateResponse(
            request, "auth/login.html", {"error": "Dieses Konto ist deaktiviert. Bitte melde dich beim Vorstand.", "next": next}
        )

    request.session.clear()
    start_session(request, pilot)
    return RedirectResponse(next or "/dashboard", status_code=303)


@router.post("/view-as/end")
def view_as_end(request: Request, db: Session = Depends(get_db)):
    """Back to the admin's own account after "Als Pilot ansehen"."""
    admin_id = request.session.pop("viewing_as_admin_id", None)
    admin_stamp = request.session.pop("viewing_as_admin_stamp", None)
    request.session.pop("viewing_as_name", None)
    admin = db.get(Pilot, admin_id) if admin_id is not None else None
    # Still an admin, with the same password as when "Als Pilot ansehen" started.
    if admin is None or not admin.is_admin or admin_stamp != session_stamp(admin):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    start_session(request, admin)
    return RedirectResponse("/admin/pilots", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html", {"sent": False})


@router.post("/forgot-password")
def forgot_password_submit(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    pilot = db.scalar(select(Pilot).where(Pilot.email == email))
    # Always show the same "sent" response regardless of whether the account exists,
    # so this endpoint can't be used to discover which emails are registered.
    # At most a few mails an hour, so nobody can flood a pilot's mailbox.
    keys = (f"mail:{email}", f"ip:{client_ip(request)}")
    if pilot is not None and not reset_throttle.blocked(*keys):
        reset_throttle.record(*keys)
        raw_token, token_hash = generate_token()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.reset_token_ttl_minutes)
        db.add(PasswordResetToken(pilot_id=pilot.id, token_hash=token_hash, expires_at=expires_at))
        db.commit()
        reset_link = f"{settings.base_url}/reset-password/{raw_token}"
        sender.send(
            to=pilot.email,
            subject="SG Bern Flugbetrieb - Passwort zurücksetzen",
            body=(
                f"Hallo {pilot.full_name}\n\n"
                f"Mit diesem Link kannst du ein neues Passwort setzen. Er ist "
                f"{settings.reset_token_ttl_minutes} Minuten gültig.\n\n{reset_link}\n\n"
                "Falls du das nicht angefordert hast, kannst du diese E-Mail ignorieren."
            ),
        )
    return templates.TemplateResponse(request, "auth/forgot_password.html", {"sent": True})


@router.get("/reset-password/{token}")
def reset_password_form(request: Request, token: str):
    return templates.TemplateResponse(request, "auth/reset_password.html", {"token": token, "error": None})


@router.post("/reset-password/{token}")
def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    token_hash = hash_token(token)
    reset_token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    now = datetime.now(timezone.utc)
    expires_at = reset_token.expires_at if reset_token else None
    if expires_at is not None and expires_at.tzinfo is None:
        # SQLite (used in tests) drops tzinfo on round-trip; Postgres preserves it.
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if (
        reset_token is None
        or reset_token.used_at is not None
        or expires_at < now
    ):
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            {"token": token, "error": "Dieser Link ist ungültig oder abgelaufen. Fordere einen neuen an."},
        )
    if problem := password_problem(password):
        return templates.TemplateResponse(
            request, "auth/reset_password.html", {"token": token, "error": problem}, status_code=400
        )

    pilot = db.get(Pilot, reset_token.pilot_id)
    pilot.password_hash = hash_password(password)  # also ends all sessions (deps.session_stamp)
    for other in db.scalars(select(PasswordResetToken).where(PasswordResetToken.pilot_id == pilot.id,
                                                             PasswordResetToken.used_at.is_(None))).all():
        other.used_at = now  # every other link sent earlier is void now too
    db.commit()

    return RedirectResponse("/login", status_code=303)
