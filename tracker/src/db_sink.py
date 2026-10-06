"""Persists detected flights of club gliders into the shared database and
matches QR-code glider claims to the takeoff that follows them.

The web app reads the `flights` table for the dashboard, logbook and
corrections. Only aircraft in the `gliders` table are written; they are
matched by OGN device address (`gliders.ogn_device_id`), with the
registration as fallback, so tracking does not depend on the OGN device
database download succeeding.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from airports import Airport
from detection import EventKind, FlightEvent, FlightRecord
from flight_tracker import is_too_short
from shared.models import AircraftKind, Airfield, Flight, FlightSource, Glider, GliderClaim, LaunchMethod

# A glider and its tow plane start their takeoff roll together; detected
# takeoff times differ by a few seconds (different speed/beacon timing).
TOW_WINDOW = timedelta(seconds=60)
# A takeoff is reported once the aircraft has climbed 50 m, so the tow plane's
# may arrive a minute or so after the glider's. A glider that no tow plane took
# off with by this long after its takeoff was launched by the winch.
WINCH_AFTER = timedelta(minutes=3)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class DbSink:
    def __init__(self, session_factory: sessionmaker, min_flight_duration_min: float = 1.0,
                 fleet_cache_s: float = 300.0):
        self._session_factory = session_factory
        self._min_duration = timedelta(minutes=min_flight_duration_min)
        self._fleet_cache_s = fleet_cache_s
        self._fleet: dict[str, str] = {}
        self._fleet_loaded_at = float("-inf")

    # ----------------------------------------------------------------- events

    def handle(self, event: FlightEvent) -> None:
        with self._session_factory() as db:
            try:
                glider = self._find_glider(db, event.flight)
                if glider is None:
                    return  # not one of ours
                flight = db.scalar(select(Flight).where(Flight.record_id == event.flight.record_id))
                if flight is None:
                    # Normally created on takeoff; if that failed (database was
                    # down) the landing creates it, so the flight isn't lost.
                    flight = self._create_flight(db, glider, event.flight)
                if event.kind is EventKind.LANDING:
                    self._record_landing(db, flight, event.flight)
                self._settle_launches(db, _aware(event.flight.landing_time or event.flight.takeoff_time))
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _find_glider(self, db: Session, record: FlightRecord) -> Optional[Glider]:
        conditions = [Glider.ogn_device_id == record.address]
        if record.registration:
            conditions.append(Glider.registration == record.registration)
        gliders = db.scalars(select(Glider).where(Glider.active.is_(True), or_(*conditions))).all()
        # Prefer the device address match: a registration in the OGN database
        # can be stale when a FLARM moved to another aircraft.
        return next((g for g in gliders if g.ogn_device_id == record.address), gliders[0] if gliders else None)

    def _create_flight(self, db: Session, glider: Glider, record: FlightRecord) -> Flight:
        takeoff_time = _aware(record.takeoff_time)
        flight = Flight(
            record_id=record.record_id,
            glider_id=glider.id,
            takeoff_time=takeoff_time,
            takeoff_airfield_icao=self._airfield(db, record.takeoff_airport),
            takeoff_estimated=record.takeoff_estimated,
            source=FlightSource.AUTO,
        )
        if glider.kind is not AircraftKind.GLIDER:
            flight.launch_method = LaunchMethod.SELF
        if glider.kind is AircraftKind.TOWPLANE:
            # Vereinsflieger: tow flights are Flugart F, billed with the towed glider.
            flight.flight_type, flight.billing = "F", "none"
        # The most recent check-in wins: someone checking in for one flight on
        # the tow plane takes precedence over the tow pilot's whole-day claim,
        # which applies again afterwards.
        claim = db.scalar(
            select(GliderClaim)
            .where(GliderClaim.glider_id == glider.id, GliderClaim.active_at(takeoff_time))
            .order_by(GliderClaim.claimed_at.desc())
        )
        if claim is not None:
            flight.pilot_id = claim.pilot_id
        db.add(flight)
        db.flush()  # need flight.id before we can link the claim to it
        if claim is not None and not claim.whole_day:
            claim.consumed_at = takeoff_time
            claim.flight_id = flight.id
        self._link_tow(db, glider, flight)
        return flight

    def _link_tow(self, db: Session, glider: Glider, flight: Flight) -> None:
        """Pairs a glider takeoff with the tow plane or motor glider taking off
        with it (same airfield, within TOW_WINDOW; the closest one if there are
        several). Whichever of the two is detected second makes the link."""
        if flight.takeoff_airfield_icao is None or flight.takeoff_estimated:
            return
        if glider.kind.can_tow and not glider.tows:
            return  # a private motor glider: never anyone's tow plane
        query = select(Flight).join(Glider, Flight.glider_id == Glider.id)
        if glider.tows:
            query = query.where(Glider.kind == AircraftKind.GLIDER)
        else:
            query = query.where(Glider.kind.in_([kind for kind in AircraftKind if kind.can_tow]), Glider.club_owned())
        candidates = db.scalars(
            query.where(Flight.id != flight.id, Flight.deleted_at.is_(None))
            .where(Flight.takeoff_airfield_icao == flight.takeoff_airfield_icao,
                   Flight.takeoff_estimated.is_(False))
            .where(Flight.takeoff_time.between(flight.takeoff_time - TOW_WINDOW, flight.takeoff_time + TOW_WINDOW))
        ).all()
        if glider.tows:
            candidates = [f for f in candidates if f.tow_flight_id is None]
        else:
            towing = set(db.scalars(select(Flight.tow_flight_id).where(Flight.tow_flight_id.is_not(None))).all())
            candidates = [f for f in candidates if f.id not in towing]
        if not candidates:
            return
        other = min(candidates, key=lambda f: abs(_aware(f.takeoff_time) - _aware(flight.takeoff_time)))
        towed, tow = (other, flight) if glider.tows else (flight, other)
        towed.tow_flight_id = tow.id
        towed.launch_method = LaunchMethod.AEROTOW
        if towed.tow_glider_id is None:
            towed.tow_glider_id = tow.glider_id
        # Vereinsflieger: a tow is Flugart F, billed with the towed glider (a
        # tow plane's flights start out that way, a motor glider's become so).
        tow.flight_type, tow.billing = "F", "none"

    def _record_landing(self, db: Session, flight: Flight, record: FlightRecord) -> None:
        if is_too_short(record, self._min_duration):
            # A bounce or ground movement, not a flight. Drop the row and give
            # a consumed claim back so it matches the pilot's real takeoff.
            claim = db.scalar(select(GliderClaim).where(GliderClaim.flight_id == flight.id))
            if claim is not None:
                claim.consumed_at = None
                claim.flight_id = None
            tow = flight.tow_flight
            if tow is not None and tow.glider is not None and tow.glider.kind is AircraftKind.MOTORGLIDER:
                tow.flight_type, tow.billing = "N", "pilot"  # it didn't tow after all
            for towed in db.scalars(select(Flight).where(Flight.tow_flight_id == flight.id)).all():
                towed.tow_flight_id = towed.tow_glider_id = None
                towed.launch_method = LaunchMethod.WINCH if self._launch_seen(towed) else None
            # Write the cleared links first: the ORM doesn't know they point at
            # this row and might delete it before (Postgres refuses that).
            db.flush()
            db.delete(flight)
            return
        duration = record.duration
        flight.landing_time = _aware(record.landing_time)
        flight.duration_min = round(duration.total_seconds() / 60, 1) if duration is not None else None
        flight.landing_airfield_icao = self._airfield(db, record.landing_airport)
        flight.landing_estimated = record.landing_estimated
        # Always kept: for an outlanding (no airfield nearby) this is the only
        # location we have, and pilots fill in the real place on correction.
        flight.landing_latitude = record.landing_latitude
        flight.landing_longitude = record.landing_longitude
        glider = flight.glider
        if (glider is not None and glider.kind is AircraftKind.GLIDER and flight.launch_method is None
                and self._launch_seen(flight)):
            flight.launch_method = LaunchMethod.WINCH  # landed, and no tow plane took off with it

    @staticmethod
    def _launch_seen(flight: Flight) -> bool:
        """The takeoff was seen at an airfield, so a tow plane taking off with
        it would have been seen too (same conditions as in _link_tow)."""
        return flight.takeoff_airfield_icao is not None and not flight.takeoff_estimated

    def _settle_launches(self, db: Session, now: Optional[datetime]) -> None:
        """Gliders are launched by a tow plane or the winch. Once WINCH_AFTER
        has passed without a tow plane taking off with a glider, it was the
        winch. Runs with every takeoff and landing of any club aircraft, which
        on a flying day is often enough - and a glider's own landing settles it
        at the latest."""
        if now is None:
            return
        pending = db.scalars(
            select(Flight).join(Glider, Flight.glider_id == Glider.id)
            .where(Glider.kind == AircraftKind.GLIDER, Flight.source == FlightSource.AUTO,
                   Flight.launch_method.is_(None), Flight.tow_flight_id.is_(None), Flight.deleted_at.is_(None))
            .where(Flight.takeoff_time.between(now - timedelta(days=1), now - WINCH_AFTER))
        ).all()
        for flight in pending:
            if self._launch_seen(flight):
                flight.launch_method = LaunchMethod.WINCH

    def _airfield(self, db: Session, airport: Optional[Airport]) -> Optional[str]:
        """Returns the airfield code, adding the airfield to the table first if
        it's new - e.g. a cross-country landing at Kägiswil (LSPG)."""
        if airport is None:
            return None
        if db.get(Airfield, airport.icao) is None:
            db.add(Airfield(icao=airport.icao, name=airport.name, latitude=airport.lat,
                            longitude=airport.lon, elevation_m=airport.elevation_m))
            db.flush()
        return airport.icao

    # ----------------------------------------------------------------- startup

    def open_flights(self, max_age: timedelta, airport_by_code) -> list[FlightRecord]:
        """Flights that took off within `max_age` and have no landing yet, e.g.
        because the tracker was restarted while they were in the air. The
        detector re-attaches them (FlightDetector.restore) so their landing
        completes the same row instead of being lost."""
        since = datetime.now(timezone.utc) - max_age
        with self._session_factory() as db:
            rows = db.execute(
                select(Flight, Glider)
                .join(Glider, Flight.glider_id == Glider.id)
                .where(Flight.source == FlightSource.AUTO, Flight.landing_time.is_(None), Flight.deleted_at.is_(None))
                .where(Flight.takeoff_time > since, Glider.ogn_device_id.is_not(None))
            ).all()
            return [
                FlightRecord(
                    address=glider.ogn_device_id.upper(),
                    registration=glider.registration,
                    model=glider.model or "",
                    takeoff_time=_aware(flight.takeoff_time),
                    takeoff_airport=airport_by_code(flight.takeoff_airfield_icao) if flight.takeoff_airfield_icao else None,
                    takeoff_estimated=flight.takeoff_estimated,
                    record_id=flight.record_id,
                )
                for flight, glider in rows
            ]

    def fleet(self) -> dict[str, str]:
        """OGN address -> registration of all active club gliders. Cached, so an
        admin can add a glider in the web app without restarting the tracker."""
        if time.monotonic() - self._fleet_loaded_at > self._fleet_cache_s:
            try:
                with self._session_factory() as db:
                    rows = db.execute(select(Glider.ogn_device_id, Glider.registration).where(
                        Glider.active.is_(True), Glider.ogn_device_id.is_not(None))).all()
                self._fleet = {address.upper(): registration for address, registration in rows}
            except Exception as exc:  # noqa: BLE001 - keep the last known fleet
                print(f"Could not load club gliders: {exc}", file=sys.stderr)
            self._fleet_loaded_at = time.monotonic()
        return self._fleet
