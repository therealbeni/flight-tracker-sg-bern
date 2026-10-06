"""Who and what is where on a flying day: the FDL's overview.

A pilot is *present* from their first check-in or flight of the day until they
check out (Auschecken: flights confirmed, going home). Checking in or flying
again after that makes them present again.

An aircraft is *in the air* (open flight), *eingecheckt* (someone has an
active check-in on it) or *frei*. Members' private aircraft are never "frei"
(not the club's to hand out): they only show up while in use or once flown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from models import Checkout, Flight, Glider, GliderClaim, Pilot
from timeutil import as_utc, local_day_bounds, to_local, today_local


@dataclass
class PilotDay:
    pilot: Pilot
    flights: list[Flight] = field(default_factory=list)
    claims: list[GliderClaim] = field(default_factory=list)  # active check-ins
    last_activity: Optional[datetime] = None  # latest check-in or takeoff
    checkout: Optional[Checkout] = None

    @property
    def airborne(self) -> Optional[Flight]:
        return next((f for f in self.flights if f.landing_time is None and f.pilot_id == self.pilot.id), None) \
            or next((f for f in self.flights if f.landing_time is None), None)

    @property
    def checked_out_at(self) -> Optional[datetime]:
        """When they checked out - unless they came back after that."""
        if self.checkout is None:
            return None
        at = as_utc(self.checkout.checked_out_at)
        return at if self.last_activity is None or at >= self.last_activity else None

    @property
    def status(self) -> str:
        """"air", "checked_in", "ground" (landed, no check-in) or "out"."""
        if self.airborne is not None:
            return "air"
        if self.checked_out_at is not None:
            return "out"
        return "checked_in" if self.claims else "ground"

    @property
    def unconfirmed(self) -> list[Flight]:
        return [f for f in self.flights if f.landing_time is not None and not f.verified_by_pilot
                and f.finalized_at is None]


@dataclass
class AircraftDay:
    glider: Glider
    airborne: Optional[Flight] = None
    claims: list[GliderClaim] = field(default_factory=list)  # active check-ins, newest first
    flight_count: int = 0

    @property
    def status(self) -> str:
        """"air", "claimed", "free", or "idle" (a private aircraft not in use)."""
        if self.airborne is not None:
            return "air"
        if self.claims:
            return "claimed"
        return "idle" if self.glider.is_private else "free"


# Everything a list of flights shows, loaded in a few queries instead of a
# few per flight.
FLIGHT_LIST_OPTIONS = (selectinload(Flight.glider), selectinload(Flight.pilot), selectinload(Flight.companion),
                       selectinload(Flight.tow_glider), selectinload(Flight.tow_pilot),
                       selectinload(Flight.tow_flight).selectinload(Flight.glider),
                       selectinload(Flight.tow_flight).selectinload(Flight.pilot))


def flights_of_day(db: Session, day: date, pilot_id: Optional[int] = None) -> list[Flight]:
    start, end = local_day_bounds(day)
    query = (select(Flight).where(Flight.deleted_at.is_(None))
             .where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
             .options(*FLIGHT_LIST_OPTIONS))
    if pilot_id is not None:
        query = query.where(or_(Flight.pilot_id == pilot_id, Flight.companion_id == pilot_id))
    return list(db.scalars(query.order_by(Flight.takeoff_time)).all())


def possible_duplicates(flights: list[Flight]) -> set[int]:
    """Flights of the same aircraft whose times overlap - one aircraft can't
    fly twice at once. Typically a flight added by hand that the tracker saw
    after all (or the other way round)."""
    def span(f: Flight) -> tuple[datetime, datetime]:
        start = as_utc(f.takeoff_time)
        return start, as_utc(f.landing_time) or start + timedelta(minutes=1)

    found = set()
    for i, a in enumerate(flights):
        for b in flights[i + 1:]:
            if a.glider_id is not None and a.glider_id == b.glider_id and a.takeoff_time and b.takeoff_time:
                (a0, a1), (b0, b1) = span(a), span(b)
                if a0 < b1 and b0 < a1:
                    found |= {a.id, b.id}
    return found


def issues(flights: list[Flight]) -> dict[int, list[str]]:
    """What's wrong with each flight that needs a look (German), by flight id."""
    duplicates = possible_duplicates(flights)
    found = {f.id: f.needs_attention + (["doppelt?"] if f.id in duplicates else []) for f in flights}
    return {fid: problems for fid, problems in found.items() if problems}


def pilots_of_day(db: Session, day: date, flights: Optional[list[Flight]] = None) -> list[PilotDay]:
    """Everyone who checked in or flew on `day`: in the air first, then
    checked in, landed, and checked out; by name within each group."""
    start, end = local_day_bounds(day)
    now = datetime.now(timezone.utc)
    flights = flights_of_day(db, day) if flights is None else flights
    claims_of_day = db.scalars(
        select(GliderClaim).where(GliderClaim.claimed_at >= start, GliderClaim.claimed_at < end)
        .options(selectinload(GliderClaim.glider), selectinload(GliderClaim.pilot))
    ).all()
    days: dict[int, PilotDay] = {}

    def entry(pilot: Pilot) -> PilotDay:
        return days.setdefault(pilot.id, PilotDay(pilot))

    def seen(pd: PilotDay, when: Optional[datetime]) -> None:
        when = as_utc(when)
        if when is not None and (pd.last_activity is None or when > pd.last_activity):
            pd.last_activity = when

    for claim in claims_of_day:
        pd = entry(claim.pilot)
        seen(pd, claim.claimed_at)
        claim_active = (claim.consumed_at is None and claim.cancelled_at is None
                        and as_utc(claim.expires_at) > now)
        if claim_active:
            pd.claims.append(claim)
    for flight in flights:
        for person in (flight.pilot, flight.companion):
            if person is not None:
                pd = entry(person)
                pd.flights.append(flight)
                seen(pd, flight.takeoff_time)
    if days:
        for checkout in db.scalars(select(Checkout).where(Checkout.day == day, Checkout.pilot_id.in_(days))).all():
            days[checkout.pilot_id].checkout = checkout

    order = {"air": 0, "checked_in": 1, "ground": 2, "out": 3}
    return sorted(days.values(), key=lambda pd: (order[pd.status], pd.pilot.full_name))


def aircraft_of_day(db: Session, day: date, flights: list[Flight]) -> list[AircraftDay]:
    """Every active club aircraft with what it's doing (in the air, checked
    in, free), then the private aircraft that flew or are checked in."""
    gliders = db.scalars(select(Glider).where(Glider.active.is_(True)).order_by(Glider.kind, Glider.registration)).all()
    board = {g.id: AircraftDay(g) for g in gliders}
    for flight in flights:
        if flight.glider_id in board:
            board[flight.glider_id].flight_count += 1
            if flight.landing_time is None:
                board[flight.glider_id].airborne = flight
    if day == today_local():
        claims = db.scalars(
            select(GliderClaim).where(GliderClaim.active_at(datetime.now(timezone.utc)))
            .options(selectinload(GliderClaim.pilot)).order_by(GliderClaim.claimed_at.desc())
        ).all()
        for claim in claims:
            if claim.glider_id in board:
                board[claim.glider_id].claims.append(claim)
    club = [a for a in board.values() if not a.glider.is_private]
    private = [a for a in board.values() if a.glider.is_private and (a.status != "idle" or a.flight_count)]
    return club + private


def record_checkout(db: Session, pilot: Pilot, day: date, by: Pilot) -> None:
    now = datetime.now(timezone.utc)
    checkout = db.scalar(select(Checkout).where(Checkout.pilot_id == pilot.id, Checkout.day == day))
    if checkout is None:
        try:
            with db.begin_nested():
                db.add(Checkout(pilot_id=pilot.id, day=day, checked_out_at=now, by_pilot_id=by.id))
            return
        except IntegrityError:  # the same checkout sent twice at once (double tap)
            checkout = db.scalar(select(Checkout).where(Checkout.pilot_id == pilot.id, Checkout.day == day))
    checkout.checked_out_at, checkout.by_pilot_id = now, by.id


def month_calendar(db: Session, first: date) -> list[list[Optional[dict]]]:
    """Monday-first weeks of the month starting at `first`; each day is None
    (another month) or {"date", "flights", "confirmed", "state"} with state
    "none" (no flights), "open" or "closed" (finalized)."""
    next_first = (first + timedelta(days=32)).replace(day=1)
    start, _ = local_day_bounds(first)
    end, _ = local_day_bounds(next_first)
    in_month = db.scalars(select(Flight).where(Flight.deleted_at.is_(None), Flight.takeoff_time >= start,
                                               Flight.takeoff_time < end)).all()
    by_day: dict[date, list[Flight]] = {}
    for f in in_month:
        by_day.setdefault(to_local(f.takeoff_time).date(), []).append(f)

    weeks, week = [], [None] * first.weekday()
    for offset in range((next_first - first).days):
        day = first + timedelta(days=offset)
        flights = by_day.get(day, [])
        week.append({"date": day, "flights": len(flights),
                     "confirmed": sum(1 for f in flights if f.verified_by_pilot),
                     "state": "none" if not flights else ("closed" if all(f.finalized_at for f in flights) else "open")})
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        weeks.append(week + [None] * (7 - len(week)))
    return weeks


def parse_month(text: Optional[str], default: date) -> date:
    """"2026-10" -> 1.10.2026; the month of `default` if it isn't one."""
    try:
        year, month = (int(part) for part in (text or "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return default.replace(day=1)
