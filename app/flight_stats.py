"""Statistik: numbers for the club and for each pilot, from the flights table.

Pure functions over a list of flights (load them with flights_in), so the
rules are easy to test:
  - A flight counts in the year/month of its takeoff (local time); deleted
    flights never count. Flight time only counts once a flight has landed.
  - Club statistics count every aircraft the tracker follows, members'
    private aircraft included (marked "privat").
  - A pilot's flights are those as pilot ("PIC") plus those as Begleiter
    (instructor, passenger), shown separately.
  - A tow is a flight that towed a glider (linked by the tracker or entered),
    or a flight of Flugart F.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from day_board import FLIGHT_LIST_OPTIONS
from models import AircraftKind, Flight, Glider, LaunchMethod, Pilot
from timeutil import as_utc, local_day_bounds, to_local

MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember"]
# Passengers may only be carried with 3 launches in the last 90 days (EASA SFCL.160).
RECENCY_DAYS = 90
RECENCY_LAUNCHES = 3


@dataclass
class Tally:
    """Flights and flight time of some group of flights."""

    flights: int = 0
    minutes: float = 0.0
    longest: float = 0.0

    def add(self, flight: Flight) -> None:
        self.flights += 1
        if flight.landing_time is not None and flight.duration_min:
            self.minutes += flight.duration_min
            self.longest = max(self.longest, flight.duration_min)

    @property
    def average(self) -> float:
        return self.minutes / self.flights if self.flights else 0.0


@dataclass
class AircraftRow:
    glider: Glider
    total: Tally = field(default_factory=Tally)
    launches: dict[str, int] = field(default_factory=dict)  # Startart code -> count
    tows: int = 0


@dataclass
class PersonRow:
    pilot: Pilot
    as_pilot: Tally = field(default_factory=Tally)
    as_companion: Tally = field(default_factory=Tally)


@dataclass
class Stats:
    total: Tally = field(default_factory=Tally)
    months: list[Tally] = field(default_factory=lambda: [Tally() for _ in range(12)])
    launches: dict[str, int] = field(default_factory=dict)
    aircraft: list[AircraftRow] = field(default_factory=list)
    tows: int = 0


@dataclass
class ClubStats(Stats):
    pilots: list[PersonRow] = field(default_factory=list)
    flying_days: int = 0


@dataclass
class PilotStats(Stats):
    as_pilot: Tally = field(default_factory=Tally)
    as_companion: Tally = field(default_factory=Tally)
    first: Optional[date] = None
    last: Optional[date] = None
    recent: Tally = field(default_factory=Tally)  # as pilot, last RECENCY_DAYS days
    longest_flight: Optional[Flight] = None


def year_bounds(year: Optional[int]) -> tuple[Optional[datetime], Optional[datetime]]:
    if year is None:
        return None, None
    return local_day_bounds(date(year, 1, 1))[0], local_day_bounds(date(year + 1, 1, 1))[0]


def flights_in(db: Session, year: Optional[int] = None, pilot: Optional[Pilot] = None) -> list[Flight]:
    """Flights of `year` (all years if None), of `pilot` if given."""
    start, end = year_bounds(year)
    query = (select(Flight).where(Flight.deleted_at.is_(None), Flight.takeoff_time.is_not(None))
             .options(*FLIGHT_LIST_OPTIONS, selectinload(Flight.glider).selectinload(Glider.owners)))
    if start is not None:
        query = query.where(Flight.takeoff_time >= start, Flight.takeoff_time < end)
    if pilot is not None:
        query = query.where(or_(Flight.pilot_id == pilot.id, Flight.companion_id == pilot.id))
    return list(db.scalars(query.order_by(Flight.takeoff_time)).all())


def years_with_flights(db: Session) -> list[int]:
    times = db.scalars(select(Flight.takeoff_time).where(Flight.deleted_at.is_(None),
                                                         Flight.takeoff_time.is_not(None))).all()
    return sorted({to_local(t).year for t in times}, reverse=True)


def _tow_ids(flights: Iterable[Flight]) -> set[int]:
    """Flights that towed a glider."""
    flights = list(flights)
    linked = {f.tow_flight_id for f in flights if f.tow_flight_id is not None}
    return linked | {f.id for f in flights if f.flight_type == "F" and f.glider and f.glider.kind.can_tow}


def _launch(flight: Flight) -> Optional[str]:
    return flight.launch_method.value if flight.launch_method else None


def _count(stats: Stats, flight: Flight, tows: set[int], rows: dict[int, AircraftRow]) -> None:
    stats.total.add(flight)
    stats.months[to_local(flight.takeoff_time).month - 1].add(flight)
    if (code := _launch(flight)) is not None:
        stats.launches[code] = stats.launches.get(code, 0) + 1
    if flight.id in tows:
        stats.tows += 1
    if flight.glider is not None:
        row = rows.setdefault(flight.glider.id, AircraftRow(flight.glider))
        row.total.add(flight)
        if code is not None:
            row.launches[code] = row.launches.get(code, 0) + 1
        if flight.id in tows:
            row.tows += 1


def _by_hours(rows: Iterable) -> list:
    return sorted(rows, key=lambda r: (-r.total.minutes, -r.total.flights, r.glider.registration))


def club_stats(flights: list[Flight]) -> ClubStats:
    stats = ClubStats()
    club = flights
    tows = _tow_ids(club)
    rows: dict[int, AircraftRow] = {}
    people: dict[int, PersonRow] = {}
    for flight in club:
        _count(stats, flight, tows, rows)
        if flight.pilot is not None:
            people.setdefault(flight.pilot.id, PersonRow(flight.pilot)).as_pilot.add(flight)
        if flight.companion is not None:
            people.setdefault(flight.companion.id, PersonRow(flight.companion)).as_companion.add(flight)
    stats.aircraft = _by_hours(rows.values())
    stats.pilots = sorted(people.values(), key=lambda p: (-p.as_pilot.minutes - p.as_companion.minutes,
                                                          p.pilot.full_name))
    stats.flying_days = len({to_local(f.takeoff_time).date() for f in club})
    return stats


def pilot_stats(flights: list[Flight], pilot: Pilot, now: Optional[datetime] = None) -> PilotStats:
    """`flights`: the pilot's flights (as pilot or Begleiter)."""
    now = now or datetime.now(timezone.utc)
    stats = PilotStats()
    tows = _tow_ids(flights)
    rows: dict[int, AircraftRow] = {}
    for flight in flights:
        _count(stats, flight, tows, rows)
        if flight.pilot_id == pilot.id:
            stats.as_pilot.add(flight)
            if as_utc(flight.takeoff_time) >= now - timedelta(days=RECENCY_DAYS):
                stats.recent.add(flight)
        else:
            stats.as_companion.add(flight)
        if flight.duration_min and (stats.longest_flight is None
                                    or flight.duration_min > (stats.longest_flight.duration_min or 0)):
            stats.longest_flight = flight
    if flights:
        stats.first = to_local(flights[0].takeoff_time).date()
        stats.last = to_local(flights[-1].takeoff_time).date()
    stats.aircraft = _by_hours(rows.values())
    return stats


def recency_ok(stats: PilotStats) -> bool:
    return stats.recent.flights >= RECENCY_LAUNCHES


def nice_max(value: float) -> float:
    """A round upper end for an axis: 1, 2, 5, 10, 20, 50 ..."""
    if value <= 0:
        return 1
    step = 1
    while step * 10 <= value:
        step *= 10
    for factor in (1, 2, 5, 10):
        if value <= step * factor:
            return step * factor
    return step * 10


def month_chart(months: list[Tally], hours: bool = True) -> dict:
    """Geometry for a 12-month column chart (templates/statistik/statistik.html):
    flight hours (or flights) per month, with three clean axis ticks."""
    values = [m.minutes / 60 if hours else m.flights for m in months]
    top = nice_max(max(values))
    return {
        "top": top,
        "ticks": [top * i / 2 for i in range(3)],
        "columns": [{"label": MONTHS[i], "name": MONTH_NAMES[i], "value": v, "share": v / top,
                     "flights": months[i].flights, "minutes": months[i].minutes} for i, v in enumerate(values)],
        "hours": hours,
    }


LAUNCH_LABELS = {m.value: m.label for m in LaunchMethod}
KIND_LABELS = {k: k.label for k in AircraftKind}
