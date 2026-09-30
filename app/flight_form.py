"""Editing and adding flights: who may, what's valid, what's recorded.

Used by the Flugbuch (club PC) and the flight page (phone). The fields follow
Vereinsflieger's Flugdatenerfassung. Every change to a flight is written to
the audit trail (FlightAuditEntry).

Who may edit a flight (can_edit):
  - nobody, once its day is finalized (an admin must re-open the day);
  - admins and the club PC account (Startstelle): every flight;
  - pilots: their own flights (as pilot or Begleiter) and flights without pilot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (BILLING_TYPES, FLIGHT_TYPES, AircraftKind, Airfield, Flight, FlightAuditEntry, FlightSource,
                    Glider, GliderClaim, LaunchMethod, Pilot, PilotRole, PilotStatus)
from timeutil import as_utc, combine_local, fmt_date, fmt_time, parse_date, to_local, today_local

# Field names in the audit trail -> what the history table shows.
FIELD_LABELS = {
    "glider_id": "Flugzeug", "pilot_id": "Pilot", "pilot_name": "Pilot (Gast)", "companion_id": "Begleiter",
    "companion_name": "Begleiter (Gast)", "launch_method": "Startart", "flight_type": "Flugart",
    "billing": "Abrechnungsart", "billing_member_id": "Zahlt", "tow_glider_id": "Schleppflugzeug",
    "tow_pilot_id": "Schlepppilot", "takeoff_airfield_icao": "Startort", "takeoff_time": "Start",
    "landing_airfield_icao": "Landeort", "landing_time": "Landung", "landings": "Landungen", "notes": "Bemerkung",
    "created": "Flug erfasst", "deleted": "Flug gelöscht",
}

# Special values of the pilot/companion dropdowns, next to member ids.
GUEST = "gast"  # Begleiter: a guest, name typed in


def can_edit(flight: Flight, user: Pilot) -> bool:
    if flight.finalized_at is not None or flight.deleted_at is not None:
        return False
    if user.edits_all_flights:
        return True
    unassigned = flight.pilot_id is None and flight.pilot_name is None
    return unassigned or user.id in (flight.pilot_id, flight.companion_id)


@dataclass
class FlightInput:
    """The form fields, as submitted (all strings).

    pilot_id: a member id, or "" = unknown/guest (then pilot_name).
    companion_id: a member id, "" = none, or GUEST (then companion_name).
    """

    glider_id: str = ""
    launch_method: str = ""
    flight_type: str = "N"
    pilot_id: str = ""
    pilot_name: str = ""
    companion_id: str = ""
    companion_name: str = ""
    flight_date: str = ""
    takeoff_time: str = ""
    landing_time: str = ""
    takeoff_airfield_icao: str = ""
    landing_airfield_icao: str = ""
    billing: str = "pilot"
    billing_member_id: str = ""
    tow_glider_id: str = ""
    tow_pilot_id: str = ""
    notes: str = ""
    errors: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, db: Session, user: Pilot, day: date) -> "FlightInput":
        """An empty form for "Flug hinzufügen"."""
        form = cls(flight_date=fmt_date(day), takeoff_airfield_icao="LSZB", landing_airfield_icao="LSZB")
        if not user.edits_all_flights:
            form.pilot_id = str(user.id)  # pilots log their own flights
        form.fill_tow_defaults(db, None)
        return form

    @classmethod
    def from_flight(cls, db: Session, f: Flight, user: Pilot) -> "FlightInput":
        form = cls(
            glider_id=str(f.glider_id or ""), launch_method=f.launch_method.value if f.launch_method else "",
            flight_type=f.flight_type, pilot_id=str(f.pilot_id or ""), pilot_name=f.pilot_name or "",
            companion_id=str(f.companion_id) if f.companion_id else (GUEST if f.companion_name else ""),
            companion_name=f.companion_name or "", flight_date=fmt_date(f.takeoff_time),
            takeoff_time=fmt_time(f.takeoff_time), landing_time=fmt_time(f.landing_time),
            takeoff_airfield_icao=f.takeoff_airfield_icao or "", landing_airfield_icao=f.landing_airfield_icao or "",
            billing=f.billing, billing_member_id=str(f.billing_member_id or ""),
            tow_glider_id=str(f.tow_aircraft.id) if f.tow_aircraft else "",
            tow_pilot_id=str(f.tow_pilot_id or (f.tow_flight.pilot_id if f.tow_flight else None) or ""),
            notes=f.notes or "",
        )
        if f.pilot_id is None and f.pilot_name is None and not user.edits_all_flights:
            form.pilot_id = str(user.id)  # opening a flight without pilot: most likely it was yours
        form.fill_tow_defaults(db, f.takeoff_time)
        return form

    def fill_tow_defaults(self, db: Session, when: Optional[datetime]) -> None:
        """F-Schlepp without tow data yet: the club's tow plane, and whoever is
        checked in on it (the tow pilot of the day)."""
        if not self.tow_glider_id:
            towplane = db.scalar(select(Glider).where(Glider.kind == AircraftKind.TOWPLANE, Glider.active.is_(True))
                                 .order_by(Glider.registration))
            self.tow_glider_id = str(towplane.id) if towplane else ""
        if not self.tow_pilot_id and self.tow_glider_id:
            claim = db.scalar(select(GliderClaim)
                              .where(GliderClaim.glider_id == int(self.tow_glider_id),
                                     GliderClaim.active_at(as_utc(when) or datetime.now(timezone.utc)))
                              .order_by(GliderClaim.claimed_at.desc()))
            self.tow_pilot_id = str(claim.pilot_id) if claim else ""


def _int_or_none(value: str) -> Optional[int]:
    value = value.strip()
    return int(value) if value.isdigit() else None


def save(db: Session, user: Pilot, form: FlightInput, flight: Optional[Flight] = None) -> Optional[Flight]:
    """Validates `form` and applies it to `flight` (or a new manual flight).
    Returns the flight, or None with form.errors filled in."""
    errors = form.errors
    glider = flight.glider if flight is not None and flight.glider else db.get(Glider, _int_or_none(form.glider_id) or 0)
    if glider is None:
        errors.append("Bitte ein Flugzeug wählen.")

    def member(value: str, what: str) -> Optional[int]:
        member_id = _int_or_none(value)
        if value.strip() and member_id is None and value != GUEST:
            errors.append(f"{what}: diese Person gibt es nicht.")
        if member_id is not None:
            person = db.get(Pilot, member_id)
            if person is None or person.status != PilotStatus.APPROVED:
                errors.append(f"{what}: diese Person gibt es nicht.")
        return member_id

    pilot_id = member(form.pilot_id, "Pilot")
    pilot_name = (form.pilot_name.strip() or None) if pilot_id is None else None
    companion_id = member(form.companion_id, "Begleiter")
    companion_name = (form.companion_name.strip() or None) if form.companion_id == GUEST else None
    if form.companion_id == GUEST and companion_name is None:
        errors.append("Bitte den Namen des Gasts (Begleiter) eingeben.")
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
    if form.flight_type not in FLIGHT_TYPES:
        errors.append("Unbekannte Flugart.")
    if form.billing not in BILLING_TYPES:
        errors.append("Unbekannte Abrechnungsart.")
    billing_member_id = member(form.billing_member_id, "Zahlt") if form.billing == "other_member" else None
    if form.billing == "other_member" and billing_member_id is None:
        errors.append("Bitte wählen, welches Mitglied bezahlt.")

    tow_glider_id = tow_pilot_id = None
    if launch is LaunchMethod.AEROTOW:
        tow_glider = db.get(Glider, _int_or_none(form.tow_glider_id) or 0)
        if tow_glider is None:
            errors.append("Bitte das Schleppflugzeug wählen.")
        else:
            tow_glider_id = tow_glider.id
        tow_pilot_id = member(form.tow_pilot_id, "Schlepppilot")

    try:
        day = parse_date(form.flight_date) if form.flight_date.strip() else today_local()
    except ValueError:
        day = None
        errors.append("Datum bitte als Tag.Monat.Jahr eingeben, z.B. 29.09.2026.")
    takeoff_time = landing_time = None
    if day is not None:
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

    if errors:
        return None

    if flight is None:
        flight = Flight(record_id=str(uuid.uuid4()), source=FlightSource.MANUAL)
        db.add(flight)
        db.flush()
        _audit(db, flight, user, "created", None, "von Hand")

    changes = {
        "glider_id": glider.id, "launch_method": launch, "flight_type": form.flight_type, "pilot_id": pilot_id,
        "pilot_name": pilot_name, "companion_id": companion_id, "companion_name": companion_name, **icaos,
        "billing": form.billing, "billing_member_id": billing_member_id, "tow_glider_id": tow_glider_id,
        "tow_pilot_id": tow_pilot_id, "notes": form.notes.strip() or None,
    }
    for key, value in changes.items():
        _set(db, flight, user, key, value)
    _sync_tow_flight(db, user, flight)

    # Times: only touch them if the entered minute differs from what we have,
    # so saving a flight doesn't round the tracker's seconds away.
    if fmt_date(flight.takeoff_time) + fmt_time(flight.takeoff_time) != fmt_date(takeoff_time) + fmt_time(takeoff_time):
        _set(db, flight, user, "takeoff_time", takeoff_time)
        flight.takeoff_estimated = False  # a person entered it
    if fmt_date(flight.landing_time) + fmt_time(flight.landing_time) != fmt_date(landing_time) + fmt_time(landing_time):
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


def _sync_tow_flight(db: Session, user: Pilot, flight: Flight) -> None:
    """Keeps the tracker's link to the tow plane's own flight consistent with
    what was entered: no aerotow or another tow plane -> unlink; a tow pilot
    entered here -> also the pilot of the tow plane's flight."""
    tow = flight.tow_flight
    if tow is None:
        return
    if flight.launch_method is not LaunchMethod.AEROTOW or flight.tow_glider_id != tow.glider_id:
        flight.tow_flight_id = None
        return
    if flight.tow_pilot_id is not None and tow.pilot_id != flight.tow_pilot_id and can_edit(tow, user):
        _set(db, tow, user, "pilot_id", flight.tow_pilot_id)


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
    if key in ("pilot_id", "companion_id", "tow_pilot_id", "billing_member_id"):
        person = db.get(Pilot, value)
        return person.full_name if person else str(value)
    if key in ("glider_id", "tow_glider_id"):
        aircraft = db.get(Glider, value)
        return aircraft.registration if aircraft else str(value)
    if key == "flight_type":
        return f"{value} - {FLIGHT_TYPES.get(value, '')}"
    if key == "billing":
        return BILLING_TYPES.get(value, value)
    if isinstance(value, LaunchMethod):
        return value.label
    if isinstance(value, datetime):
        return to_local(value).strftime("%d.%m. %H:%M")
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
    gliders = db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.kind, Glider.registration)).all()
    return {
        "gliders": gliders,
        "towplanes": [g for g in gliders if g.kind is AircraftKind.TOWPLANE],
        "members": members(db),
        "airfields": db.scalars(select(Airfield).order_by(Airfield.icao)).all(),
        "launch_methods": list(LaunchMethod),
        "flight_types": FLIGHT_TYPES,
        "billing_types": BILLING_TYPES,
        "guest": GUEST,
    }


async def read_form(request: Request) -> FlightInput:
    data = await request.form()
    return FlightInput(**{k: str(data.get(k, "")) for k in FORM_FIELDS})
