"""Editing and adding flights: who may, what's valid, what's recorded.

Used by the Flugbuch (club PC) and the flight page (phone). Every change to a
flight is written to the audit trail (FlightAuditEntry).

Who may edit a flight (can_edit):
  - nobody, once its day is finalized (an admin must re-open the day);
  - admins and the club PC account (Startstelle): every flight;
  - pilots: their own flights (as pilot or Begleiter) and flights without pilot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from fastapi import Request
from sqlalchemy.orm import Session

from models import (Airfield, Flight, FlightAuditEntry, FlightSource, Glider, LaunchMethod, Pilot, PilotRole,
                    PilotStatus)
from timeutil import as_utc, combine_local, fmt_time, to_local

# Field names in the audit trail -> what the history table shows.
FIELD_LABELS = {
    "glider_id": "Flugzeug", "pilot_id": "Pilot", "pilot_name": "Pilot (Gast)", "companion_id": "Begleiter",
    "companion_name": "Begleiter (Gast)", "launch_method": "Startart", "takeoff_airfield_icao": "Startort",
    "takeoff_time": "Startzeit", "landing_airfield_icao": "Landeort", "landing_time": "Landezeit",
    "landings": "Landungen", "notes": "Bemerkung", "created": "Flug erfasst", "deleted": "Flug gelöscht",
}


def can_edit(flight: Flight, user: Pilot) -> bool:
    if flight.finalized_at is not None or flight.deleted_at is not None:
        return False
    if user.edits_all_flights:
        return True
    unassigned = flight.pilot_id is None and flight.pilot_name is None
    return unassigned or user.id in (flight.pilot_id, flight.companion_id)


@dataclass
class FlightInput:
    """The form fields, as submitted (all strings)."""

    glider_id: str = ""
    pilot_id: str = ""
    pilot_name: str = ""
    companion_id: str = ""
    companion_name: str = ""
    launch_method: str = ""
    takeoff_airfield_icao: str = ""
    takeoff_time: str = ""
    landing_airfield_icao: str = ""
    landing_time: str = ""
    landings: str = "1"
    notes: str = ""
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_flight(cls, f: Flight) -> "FlightInput":
        return cls(
            glider_id=str(f.glider_id or ""), pilot_id=str(f.pilot_id or ""), pilot_name=f.pilot_name or "",
            companion_id=str(f.companion_id or ""), companion_name=f.companion_name or "",
            launch_method=f.launch_method.value if f.launch_method else "",
            takeoff_airfield_icao=f.takeoff_airfield_icao or "", takeoff_time=fmt_time(f.takeoff_time),
            landing_airfield_icao=f.landing_airfield_icao or "", landing_time=fmt_time(f.landing_time),
            landings=str(f.landings), notes=f.notes or "",
        )


def _int_or_none(value: str) -> Optional[int]:
    value = value.strip()
    return int(value) if value.isdigit() else None


def save(db: Session, user: Pilot, form: FlightInput, day: date, flight: Optional[Flight] = None) -> Optional[Flight]:
    """Validates `form` and applies it to `flight` (or a new manual flight on
    `day`). Returns the flight, or None with form.errors filled in."""
    errors = form.errors
    glider = db.get(Glider, _int_or_none(form.glider_id) or 0)
    if glider is None:
        errors.append("Bitte ein Flugzeug wählen.")

    pilot_id = _int_or_none(form.pilot_id)
    pilot_name = form.pilot_name.strip() or None if pilot_id is None else None
    companion_id = _int_or_none(form.companion_id)
    companion_name = form.companion_name.strip() or None if companion_id is None else None
    for member_id in (pilot_id, companion_id):
        member = db.get(Pilot, member_id) if member_id else None
        if member_id and (member is None or member.status != PilotStatus.APPROVED):
            errors.append("Diesen Piloten gibt es nicht.")
    if pilot_id is not None and pilot_id == companion_id:
        errors.append("Pilot und Begleiter können nicht dieselbe Person sein.")

    if not user.edits_all_flights:
        # Pilots only log flights they were on - as pilot, or as Begleiter
        # (instructor with a student or guest).
        stays_unassigned = (flight is not None and flight.pilot_id is None and flight.pilot_name is None
                            and pilot_id is None and pilot_name is None)
        if user.id not in (pilot_id, companion_id) and not stays_unassigned:
            errors.append("Du kannst nur Flüge erfassen, bei denen du Pilot oder Begleiter bist.")

    launch = None
    if form.launch_method:
        try:
            launch = LaunchMethod(form.launch_method)
        except ValueError:
            errors.append("Unbekannte Startart.")

    takeoff_time = landing_time = None
    try:
        takeoff_time = combine_local(day, form.takeoff_time) if form.takeoff_time.strip() else None
        landing_time = combine_local(day, form.landing_time) if form.landing_time.strip() else None
    except ValueError:
        errors.append("Zeiten bitte als Stunden:Minuten eingeben, z.B. 14:05.")
    if takeoff_time is None and not errors:
        errors.append("Bitte eine Startzeit eingeben.")
    if takeoff_time and landing_time and landing_time <= takeoff_time:
        errors.append("Die Landung muss nach dem Start sein.")

    icaos = {}
    for key in ("takeoff_airfield_icao", "landing_airfield_icao"):
        code = getattr(form, key).strip().upper() or None
        if code is not None and db.get(Airfield, code) is None:
            errors.append(f"Den Flugplatz «{code}» kennen wir noch nicht. Ein Admin kann ihn unter "
                          "Verwaltung > Flugplätze erfassen.")
        icaos[key] = code

    landings = _int_or_none(form.landings)
    if landings is None or landings > 99:
        errors.append("Anzahl Landungen bitte als Zahl eingeben.")

    if errors:
        return None

    created = flight is None
    if created:
        flight = Flight(record_id=str(uuid.uuid4()), source=FlightSource.MANUAL)
        db.add(flight)
        db.flush()
        _audit(db, flight, user, "created", None, "von Hand")

    changes = {
        "glider_id": glider.id, "pilot_id": pilot_id, "pilot_name": pilot_name, "companion_id": companion_id,
        "companion_name": companion_name, "launch_method": launch, **icaos, "landings": landings,
        "notes": form.notes.strip() or None,
    }
    for key, value in changes.items():
        _set(db, flight, user, key, value)

    # Times: only touch them if the entered minute differs from what we have,
    # so saving a flight doesn't round the tracker's seconds away.
    if fmt_time(flight.takeoff_time) != fmt_time(takeoff_time):
        _set(db, flight, user, "takeoff_time", takeoff_time)
        flight.takeoff_estimated = False  # a person entered it
    if fmt_time(flight.landing_time) != fmt_time(landing_time):
        _set(db, flight, user, "landing_time", landing_time)
        flight.landing_estimated = False
    if flight.takeoff_time and flight.landing_time:
        flight.duration_min = round((as_utc(flight.landing_time) - as_utc(flight.takeoff_time)).total_seconds() / 60, 1)
    else:
        flight.duration_min = None
    if flight.landing_airfield_icao is not None:
        # The real landing place is known; the raw outlanding position has served its purpose.
        flight.landing_latitude = flight.landing_longitude = None
    return flight


def delete(db: Session, user: Pilot, flight: Flight) -> None:
    flight.deleted_at = datetime.now(timezone.utc)
    _audit(db, flight, user, "deleted", None, "gelöscht")


def _set(db: Session, flight: Flight, user: Pilot, key: str, value) -> None:
    old = getattr(flight, key)
    if old != value:
        _audit(db, flight, user, key, _text(db, key, old), _text(db, key, value))
        setattr(flight, key, value)


def _text(db: Session, key: str, value) -> Optional[str]:
    """How a value reads in the change history: names, not database ids."""
    if value is None:
        return None
    if key in ("pilot_id", "companion_id"):
        person = db.get(Pilot, value)
        return person.full_name if person else str(value)
    if key == "glider_id":
        aircraft = db.get(Glider, value)
        return aircraft.registration if aircraft else str(value)
    if isinstance(value, LaunchMethod):
        return value.label
    if isinstance(value, datetime):
        return to_local(value).strftime("%H:%M")
    return str(value)


def _audit(db: Session, flight: Flight, user: Pilot, key: str, old: Optional[str], new: Optional[str]) -> None:
    db.add(FlightAuditEntry(flight_id=flight.id, changed_by_pilot_id=user.id, field_name=key,
                            old_value=_fit(old), new_value=_fit(new)))


def _fit(text: Optional[str]) -> Optional[str]:
    """The history keeps 255 characters per value (long notes are cut)."""
    return text if text is None or len(text) <= 255 else text[:254] + "…"


def members(db: Session) -> list[Pilot]:
    """Everyone who can be pilot or Begleiter (not the club PC account)."""
    return list(db.scalars(
        select(Pilot).where(Pilot.status == PilotStatus.APPROVED, Pilot.role != PilotRole.FLIGHTDESK)
        .order_by(Pilot.full_name)
    ).all())


FORM_FIELDS = [f for f in FlightInput.__dataclass_fields__ if f != "errors"]


def form_choices(db: Session) -> dict:
    """Everything the flight form's dropdowns need."""
    return {
        "gliders": db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.kind, Glider.registration)).all(),
        "members": members(db),
        "airfields": db.scalars(select(Airfield).order_by(Airfield.icao)).all(),
        "launch_methods": list(LaunchMethod),
    }


async def read_form(request: Request) -> FlightInput:
    data = await request.form()
    return FlightInput(**{k: str(data.get(k, "")) for k in FORM_FIELDS})
