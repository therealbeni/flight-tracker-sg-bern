"""Tag abschliessen sends the day to the club's Vereinsflieger (docs/vereinsflieger.md).

Only when every flight of the day is "green": landed, confirmed, nothing to
check, and everything Vereinsflieger needs is there (`problems`). Then each
flight is sent once:

- a flight already in Vereinsflieger (same aircraft, times overlapping - typed
  in by hand) is only linked to it, never sent again,
- a flight we sent before is only sent again (edited there) if someone changed
  it since (re-opened day),
- tows aren't sent on their own: the glider flight carries the tow
  (Schleppflugzeug, -pilot, -zeit) and Vereinsflieger creates the tow plane's
  flight itself.

Every flight is claimed in the database before it's sent (`_claim`), so
pressing twice, or two people at once, can't send it twice.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

import vereinsflieger as vf
import vereinsflieger_directory as directory
from day_board import flights_of_day, possible_duplicates
from models import (VEREINSFLIEGER_CHARGEMODE, VEREINSFLIEGER_FTID, VEREINSFLIEGER_STARTTYPE, Flight,
                    FlightAuditEntry, LaunchMethod, Pilot)
from timeutil import as_utc, to_local

# Marks a flight being sent right now (vereinsflieger_external_id); older
# than this, the request that set it died - look in Vereinsflieger, then send.
PENDING = "pending"
PENDING_STALE = timedelta(minutes=2)
# Who pays, by Abrechnungsart: they must be linked to their Vereinsflieger member.
PILOT_PAYS = {"pilot", "pilot_companion", "guest_pilot_pays"}
COMPANION_PAYS = {"companion", "pilot_companion"}


def enabled() -> bool:
    """Whether Tag abschliessen sends to Vereinsflieger (mode "live")."""
    return vf.Config.from_env().can_write


@dataclass
class Row:
    """One flight of the day, and what Tag abschliessen does with it."""

    flight: Flight
    problems: list[str] = field(default_factory=list)  # German; any -> the day can't be closed
    is_tow: bool = False  # sent with the glider it towed, not on its own
    fields: dict = field(default_factory=dict)

    @property
    def green(self) -> bool:
        return not self.problems


@dataclass
class Plan:
    rows: list[Row]

    @property
    def ready(self) -> bool:
        return bool(self.rows) and all(r.green for r in self.rows)

    @property
    def to_send(self) -> list[Row]:
        return [r for r in self.rows if not r.is_tow]

    def problems_of(self, flight_id: int) -> list[str]:
        return next((r.problems for r in self.rows if r.flight.id == flight_id), [])


def plan(db: Session, day: date, flights: Optional[list[Flight]] = None) -> Plan:
    flights = flights if flights is not None else flights_of_day(db, day)
    members = {e.key: e.name for e in directory.entries(db, directory.MEMBER)}
    aircraft = {e.key for e in directory.entries(db, directory.AIRCRAFT)}
    places = directory.place_names(db)
    duplicates = possible_duplicates(flights)
    towing = {f.tow_flight_id for f in flights if f.tow_flight_id}
    towed_by = {(f.tow_aircraft.id, to_local(f.takeoff_time).replace(second=0, microsecond=0))
                for f in flights if f.launch_method is LaunchMethod.AEROTOW and f.tow_aircraft}
    rows = []
    for f in flights:
        row = Row(f)
        row.problems += f.needs_attention
        if f.id in duplicates:
            row.problems.append("doppelt?")
        if f.landing_time and not f.verified_by_pilot and not f.finalized_at:
            row.problems.append("nicht bestätigt")
        row.is_tow = f.id in towing or f.flight_type == "F"
        if row.is_tow:
            if f.id not in towing and not _tows_someone(f, towed_by):
                row.problems.append("Schlepp ohne geschleppten Flug")
        else:
            row.problems += _vereinsflieger_problems(f, members, aircraft)
        if not row.problems and not row.is_tow:
            row.fields = _fields(f, members, places)
        rows.append(row)
    return Plan(rows)


def _tows_someone(tow: Flight, towed_by: set) -> bool:
    """A tow the tracker didn't link: a glider flight with this aircraft as
    Schleppflugzeug took off within 3 minutes of it."""
    start = to_local(tow.takeoff_time).replace(second=0, microsecond=0)
    return any((tow.glider_id, start + timedelta(minutes=m)) in towed_by for m in range(-3, 4))


def _member_uid(person: Optional[Pilot], members: dict) -> Optional[str]:
    uid = person.vereinsflieger_member_id if person else None
    return uid if uid in members else None


def _vereinsflieger_problems(f: Flight, members: dict, aircraft: set) -> list[str]:
    problems = []
    if aircraft and f.glider and f.glider.registration not in aircraft:
        problems.append("Flugzeug im Vereinsflieger unbekannt")
    if f.takeoff_airfield_icao is None:
        problems.append("Startort fehlt")
    if f.launch_method is None:
        problems.append("Startart fehlt")
    if f.flight_type not in VEREINSFLIEGER_FTID:
        problems.append("Flugart unbekannt")
    for person, role in [(f.pilot, "Pilot"), (f.companion, "Begleiter"), (f.billing_member, "Zahlendes Mitglied")]:
        if person is not None and not _member_uid(person, members):
            problems.append(f"{role} nicht mit Vereinsflieger verknüpft")
    if f.billing in PILOT_PAYS and f.pilot is None and f.pilot_name:
        problems.append("Pilot zahlt, ist aber kein Mitglied")
    if f.billing in COMPANION_PAYS and f.companion is None:
        problems.append("Begleiter zahlt, ist aber kein Mitglied")
    if f.billing == "other_member" and f.billing_member is None:
        problems.append("Zahlendes Mitglied fehlt")
    if f.launch_method is LaunchMethod.AEROTOW:
        if f.tow_aircraft is None:
            problems.append("Schleppflugzeug fehlt")
        if f.tow_pilot_display is None:
            problems.append("Schlepppilot fehlt")
        tow_pilot = f.tow_pilot or (f.tow_flight.pilot if f.tow_flight else None)
        if tow_pilot is not None and not _member_uid(tow_pilot, members):
            problems.append("Schlepppilot nicht mit Vereinsflieger verknüpft")
    return problems


def _fields(f: Flight, members: dict, places: dict) -> dict:
    """The flight as Vereinsflieger's flight/add takes it (tested 2026-11-07:
    local times, airfields by their exact Vereinsflieger name)."""
    fields = {
        "callsign": f.glider.registration,
        "departuretime": vf.vf_time(f.takeoff_time), "arrivaltime": vf.vf_time(f.landing_time),
        "departurelocation": places.get(f.takeoff_airfield_icao, f.takeoff_airfield_icao),
        "arrivallocation": places.get(f.landing_airfield_icao, f.landing_airfield_icao),
        "landingcount": f.landings, "starttype": VEREINSFLIEGER_STARTTYPE[f.launch_method],
        "ftid": VEREINSFLIEGER_FTID[f.flight_type], "chargemode": VEREINSFLIEGER_CHARGEMODE[f.billing],
    }
    for uid_key, name_key, person, name in [("uidpilot", "pilotname", f.pilot, f.pilot_name),
                                            ("uidattendant", "attendantname", f.companion, f.companion_name)]:
        if uid := _member_uid(person, members):
            fields[uid_key], fields[name_key] = uid, members[uid]
        elif name:
            fields[name_key] = name
    if f.billing == "other_member":
        fields["uidcharge"] = _member_uid(f.billing_member, members)
    if f.launch_method is LaunchMethod.AEROTOW:
        fields["towcallsign"] = f.tow_aircraft.registration
        tow_pilot = f.tow_pilot or (f.tow_flight.pilot if f.tow_flight else None)
        if uid := _member_uid(tow_pilot, members):
            fields["towuidpilot"], fields["towpilotname"] = uid, members[uid]
        else:
            fields["towpilotname"] = f.tow_pilot_display
        if f.tow_flight and f.tow_flight.duration_min:
            fields["towtime"] = round(f.tow_flight.duration_min)
    if f.notes:
        fields["comment"] = f.notes
    return fields


# -- sending ------------------------------------------------------------------

@dataclass
class Result:
    sent: int = 0
    edited: int = 0
    linked: int = 0  # already in Vereinsflieger (typed in by hand)
    unchanged: int = 0
    error: str = ""

    @property
    def message(self) -> str:
        parts = []
        if self.sent:
            parts.append(f"{self.sent} {'Flug' if self.sent == 1 else 'Flüge'} an den Vereinsflieger übertragen")
        if self.edited:
            parts.append(f"{self.edited} dort korrigiert")
        if self.linked:
            parts.append(f"{self.linked} {'war' if self.linked == 1 else 'waren'} schon im Vereinsflieger")
        if not parts:
            parts.append("Alle Flüge sind schon im Vereinsflieger")
        text = ", ".join(parts) + "."
        return f"{text} Fehler: {self.error}" if self.error else text


def _vf_span(v: dict) -> Optional[tuple[datetime, datetime]]:
    try:
        start = datetime.strptime(str(v.get("departuretime", ""))[:16], "%Y-%m-%d %H:%M")
        end = datetime.strptime(str(v.get("arrivaltime", ""))[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return start, max(end, start + timedelta(minutes=1))


def find_in_vereinsflieger(f: Flight, vf_flights: list[dict], taken: set[str]) -> Optional[str]:
    """The flid of the Vereinsflieger flight that is this one: same aircraft,
    times overlapping (one aircraft can't fly twice at once)."""
    start = to_local(f.takeoff_time).replace(tzinfo=None, second=0, microsecond=0)
    end = max(to_local(f.landing_time).replace(tzinfo=None, second=0, microsecond=0), start + timedelta(minutes=1))
    for v in vf_flights:
        flid = str(v.get("flid", ""))
        span = _vf_span(v)
        if (span and flid not in taken and str(v.get("deleted", "0")) in ("", "0")
                and str(v.get("callsign", "")).upper() == f.glider.registration.upper()
                and span[0] < end and start < span[1]):
            return flid
    return None


def _claim(db: Session, f: Flight, now: datetime) -> bool:
    """Marks the flight as being sent, unless it already is (or was). Committed
    at once: a second request sees it and leaves the flight alone."""
    stale = now - PENDING_STALE
    claimed = db.execute(
        update(Flight).where(Flight.id == f.id)
        .where((Flight.vereinsflieger_external_id.is_(None))
               | ((Flight.vereinsflieger_external_id == PENDING) & (Flight.vereinsflieger_synced_at < stale)))
        .values(vereinsflieger_external_id=PENDING, vereinsflieger_synced_at=now)
        .execution_options(synchronize_session=False)).rowcount == 1
    db.commit()
    db.refresh(f)
    return claimed


def _changed_since_sent(db: Session, f: Flight) -> bool:
    """Someone corrected the flight after it was sent (the day was re-opened)."""
    return f.vereinsflieger_synced_at is not None and db.scalar(
        select(FlightAuditEntry.id).where(FlightAuditEntry.flight_id == f.id,
                                          FlightAuditEntry.changed_at > as_utc(f.vereinsflieger_synced_at))
        .limit(1)) is not None


def push_day(db: Session, day: date, user: Pilot, client_factory=None) -> Result:
    """Sends the day's flights. Call after the day is closed (finalize_day,
    committed): from then on nobody changes them while we send."""
    the_plan = plan(db, day)
    result = Result()
    if not the_plan.ready:
        result.error = "Nicht alle Flüge sind bereit."
        return result
    session = client_factory or (lambda: vf.session(db, by_pilot_id=user.id))
    try:
        with session() as client:
            vf_flights = client.flights_of_day(day)
            taken = {r.flight.vereinsflieger_external_id for r in the_plan.rows if r.flight.vereinsflieger_external_id}
            for row in the_plan.to_send:
                _send(db, client, row, vf_flights, taken, result)
    except vf.VereinsfliegerError as e:
        result.error = str(e)
    return result


def _send(db: Session, client, row: Row, vf_flights: list[dict], taken: set, result: Result) -> None:
    f = row.flight
    flid = f.vereinsflieger_external_id
    if flid and flid != PENDING:
        if _changed_since_sent(db, f):
            client.edit_flight(int(flid), row.fields, flight_id=f.id)
            f.vereinsflieger_synced_at = datetime.now(timezone.utc)
            db.commit()
            result.edited += 1
        else:
            result.unchanged += 1
        return
    now = datetime.now(timezone.utc)
    if not _claim(db, f, now):
        result.unchanged += 1  # someone else is sending it right now
        return
    found = find_in_vereinsflieger(f, vf_flights, taken)
    if found:
        result.linked += 1
    else:
        try:
            created = client.add_flight(row.fields, flight_id=f.id)
        except vf.VereinsfliegerError as e:
            if e.refused:
                # Vereinsflieger said no: nothing was created, the next try sends it again.
                f.vereinsflieger_external_id = f.vereinsflieger_synced_at = None
            # Otherwise (no answer) it stays "pending": the next try looks for it
            # in Vereinsflieger first (after PENDING_STALE), and only then sends it.
            db.commit()
            raise
        found = str(created.get("flid") or "")
        if not found:
            # Sent, but no flid came back: look it up rather than risk sending it twice.
            found = find_in_vereinsflieger(f, client.flights_of_day(to_local(f.takeoff_time).date()), taken)
        if not found:
            raise vf.VereinsfliegerError(f"Flug {f.glider.registration} {vf.vf_time(f.takeoff_time)}: "
                                         "übertragen, aber im Vereinsflieger nicht wiedergefunden - bitte dort prüfen.")
        result.sent += 1
        if created.get("flidtow") and f.tow_flight:
            f.tow_flight.vereinsflieger_external_id = str(created["flidtow"])
            f.tow_flight.vereinsflieger_synced_at = now
    taken.add(found)
    f.vereinsflieger_external_id, f.vereinsflieger_synced_at = found, datetime.now(timezone.utc)
    db.commit()
