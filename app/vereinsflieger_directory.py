"""What the tracker knows of the club's Vereinsflieger: its members, aircraft
and places, as seen in the club's flights there (table vereinsflieger_entries).

An admin reloads it (Verwaltung > Vereinsflieger: one reading call for a year
of flights). From it:

- pilots are linked to their Vereinsflieger member - an admin confirms each
  link, we only suggest one when the names match,
- we know which of our aircraft Vereinsflieger knows,
- we know how Vereinsflieger names our airfields: the gliders' field at Bern is
  "Bern-Belp Gld LSZB", next to the airport "Bern-Belp LSZB" - we take the name
  the club's flights use most.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from models import Pilot, VereinsfliegerEntry

MEMBER, AIRCRAFT, PLACE = "member", "aircraft", "place"
# How far back a reload looks.
RELOAD_DAYS = 365


@dataclass
class _Seen:
    flights: int = 0
    last_flight: date = date.min
    names: Counter = field(default_factory=Counter)

    def add(self, day: date, name: str = "") -> None:
        self.flights += 1
        self.last_flight = max(self.last_flight, day)
        if name:
            self.names[name] += 1


def collect(flights: list[dict]) -> list[VereinsfliegerEntry]:
    """Members (pilots and Begleiter with a uid - guests typed as free text have
    uid 0), aircraft and places of Vereinsflieger's flight list, deleted flights
    left out."""
    seen: dict[tuple[str, str], _Seen] = {}

    def note(kind: str, key: str, day: date, name: str = "") -> None:
        key = key.strip()
        if key and key != "0":
            seen.setdefault((kind, key), _Seen()).add(day, name.strip())

    for f in flights:
        if str(f.get("deleted", "0")) not in ("", "0"):
            continue
        try:
            day = date.fromisoformat(str(f.get("dateofflight", ""))[:10])
        except ValueError:
            continue
        note(MEMBER, str(f.get("uidpilot", "")), day, str(f.get("pilotname", "")))
        note(MEMBER, str(f.get("uidattendant", "")), day, str(f.get("attendantname", "")))
        note(AIRCRAFT, str(f.get("callsign", "")).upper(), day)
        for place in {str(f.get("departurelocation", "")), str(f.get("arrivallocation", ""))}:
            note(PLACE, place, day)
    return [VereinsfliegerEntry(kind=kind, key=key, flights=s.flights, last_flight=s.last_flight,
                                name=s.names.most_common(1)[0][0] if s.names else None)
            for (kind, key), s in sorted(seen.items())]


def reload(db: Session, client, today: date) -> int:
    """Replaces what we know with the club's flights of the last year
    (`client`: a logged-in vereinsflieger.Client). Returns how many flights
    were read."""
    flights = client.flights_between(today - timedelta(days=RELOAD_DAYS), today)
    entries = collect(flights)
    now = datetime.now(timezone.utc)
    db.execute(delete(VereinsfliegerEntry))
    for e in entries:
        e.loaded_at = now
    db.add_all(entries)
    db.commit()
    return len(flights)


def loaded_at(db: Session) -> Optional[datetime]:
    return db.scalar(select(func.max(VereinsfliegerEntry.loaded_at)))


def entries(db: Session, kind: str) -> list[VereinsfliegerEntry]:
    return list(db.scalars(select(VereinsfliegerEntry).where(VereinsfliegerEntry.kind == kind)
                           .order_by(VereinsfliegerEntry.name, VereinsfliegerEntry.key)).all())


def place_names(db: Session) -> dict[str, str]:
    """ICAO code -> Vereinsflieger's name for that airfield, the one the club's
    flights use most ("Bern-Belp Gld LSZB" over "Bern-Belp LSZB")."""
    best: dict[str, VereinsfliegerEntry] = {}
    for e in entries(db, PLACE):
        if m := re.search(r"\b([A-Z]{4})$", e.key):
            icao = m.group(1)
            if icao not in best or e.flights > best[icao].flights:
                best[icao] = e
    return {icao: e.key for icao, e in best.items()}


def name_key(name: str) -> tuple[str, ...]:
    """A name in a form that matches regardless of order, commas, case and
    accents: "Bürgi, Benjamin" and "Benjamin Buergi" don't match (umlaut
    spelled out), "Bürgi, Benjamin" and "benjamin bürgi" do."""
    plain = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return tuple(sorted(re.findall(r"\w+", plain)))


def suggestions(pilots: list[Pilot], members: list[VereinsfliegerEntry]) -> dict[int, VereinsfliegerEntry]:
    """Pilot id -> the Vereinsflieger member with the same name, for pilots not
    linked yet. Only when exactly one member has that name and nobody is linked
    to them already - an admin still confirms it."""
    linked = {p.vereinsflieger_member_id for p in pilots if p.vereinsflieger_member_id}
    by_name: dict[tuple[str, ...], list[VereinsfliegerEntry]] = {}
    for m in members:
        if m.name:
            by_name.setdefault(name_key(m.name), []).append(m)
    result = {}
    for p in pilots:
        candidates = by_name.get(name_key(p.full_name), [])
        if not p.vereinsflieger_member_id and len(candidates) == 1 and candidates[0].key not in linked:
            result[p.id] = candidates[0]
    return result
