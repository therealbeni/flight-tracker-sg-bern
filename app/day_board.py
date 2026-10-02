"""Who and what is where on a flying day: the FDL's overview.

A pilot is *present* from their first check-in or flight of the day until they
check out (Auschecken: flights confirmed, going home). Checking in or flying
again after that makes them present again.

An aircraft is *in the air* (open flight), *eingecheckt* (someone has an
active check-in on it) or *frei*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from models import Checkout, Flight, Glider, GliderClaim, Pilot
from timeutil import as_utc, local_day_bounds, today_local


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
        """"air", "claimed" or "free"."""
        if self.airborne is not None:
            return "air"
        return "claimed" if self.claims else "free"


def flights_of_day(db: Session, day: date, pilot_id: Optional[int] = None) -> list[Flight]:
    start, end = local_day_bounds(day)
    query = (select(Flight).where(Flight.deleted_at.is_(None))
             .where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
             .options(selectinload(Flight.glider), selectinload(Flight.pilot), selectinload(Flight.companion),
                      selectinload(Flight.tow_glider), selectinload(Flight.tow_pilot),
                      selectinload(Flight.tow_flight).selectinload(Flight.glider),
                      selectinload(Flight.tow_flight).selectinload(Flight.pilot)))
    if pilot_id is not None:
        query = query.where(or_(Flight.pilot_id == pilot_id, Flight.companion_id == pilot_id))
    return list(db.scalars(query.order_by(Flight.takeoff_time)).all())


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
    """Every active aircraft with what it's doing: in the air, checked in, free."""
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
    return list(board.values())


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
