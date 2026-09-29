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
from shared.models import Airfield, Flight, FlightSource, Glider, GliderClaim


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
        claim = db.scalar(
            select(GliderClaim)
            .where(GliderClaim.glider_id == glider.id, GliderClaim.consumed_at.is_(None))
            .where(GliderClaim.expires_at > takeoff_time)
            .order_by(GliderClaim.claimed_at.desc())
        )
        if claim is not None:
            flight.pilot_id = claim.pilot_id
        db.add(flight)
        db.flush()  # need flight.id before we can link the claim to it
        if claim is not None:
            claim.consumed_at = takeoff_time
            claim.flight_id = flight.id
        return flight

    def _record_landing(self, db: Session, flight: Flight, record: FlightRecord) -> None:
        if is_too_short(record, self._min_duration):
            # A bounce or ground movement, not a flight. Drop the row and give
            # a consumed claim back so it matches the pilot's real takeoff.
            claim = db.scalar(select(GliderClaim).where(GliderClaim.flight_id == flight.id))
            if claim is not None:
                claim.consumed_at = None
                claim.flight_id = None
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
                .where(Flight.source == FlightSource.AUTO, Flight.landing_time.is_(None))
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
