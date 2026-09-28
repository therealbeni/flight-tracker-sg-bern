"""Persists detected flights into the shared Postgres schema and matches QR-code
glider claims to the takeoff that follows them.

This is a FilteredLogger like AirportLogger/ClubLogger so it plugs into the same
pipeline in run.py, but instead of a CSV file it writes into the `flights` table
that the web app reads from for the dashboard, logbook, and corrections UI.
"""

import sys
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from flight_record import FlightRecord
from flight_tracker import FilteredLogger
from shared.models import Flight, FlightSource, Glider, GliderClaim


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class DbSink(FilteredLogger):
    """Writes one row per flight leg of a known club glider into `flights`.

    Only registrations that exist in the `gliders` table are tracked here - this
    mirrors ClubLogger's fleet filter, but is checked against the live table (not
    a snapshot) so gliders added through the admin panel are picked up without a
    tracker restart.
    """

    def __init__(self, session_factory: sessionmaker, min_flight_duration_min: float = 1.0):
        self._session_factory = session_factory
        self._min_flight_duration_min = min_flight_duration_min
        # In-memory dedup so a flight already written isn't re-inserted/re-updated
        # on every subsequent beacon of the same aircraft while it's airborne.
        self._created_record_ids: set[str] = set()
        self._landed_record_ids: set[str] = set()

    def log(self, flight_record: FlightRecord) -> None:
        if flight_record.takeoff_time is None:
            return  # not airborne yet, nothing to persist

        db: Session = self._session_factory()
        try:
            glider = db.scalar(select(Glider).where(Glider.registration == flight_record.callsign))
            if glider is None:
                return  # not one of ours - no pilot/logbook features apply

            if flight_record.record_id not in self._created_record_ids:
                self._create_flight(db, glider, flight_record)
                self._created_record_ids.add(flight_record.record_id)
                return

            if flight_record.landing_time is not None and flight_record.record_id not in self._landed_record_ids:
                self._record_landing(db, flight_record)
                self._landed_record_ids.add(flight_record.record_id)
        except Exception as exc:  # noqa: BLE001 - never let a DB hiccup kill the tracker
            db.rollback()
            print(f"DbSink error for {flight_record.callsign}: {exc}", file=sys.stderr)
        finally:
            db.close()

    def _create_flight(self, db: Session, glider: Glider, flight_record: FlightRecord) -> None:
        takeoff_time = _aware(flight_record.takeoff_time)
        takeoff_icao = flight_record.takeoff_airport.icao if flight_record.takeoff_airport else None

        flight = Flight(
            record_id=flight_record.record_id,
            glider_id=glider.id,
            takeoff_time=takeoff_time,
            takeoff_airfield_icao=takeoff_icao,
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

        db.commit()

    def _record_landing(self, db: Session, flight_record: FlightRecord) -> None:
        flight = db.scalar(select(Flight).where(Flight.record_id == flight_record.record_id))
        if flight is None:
            return  # takeoff row somehow missing - nothing to update

        duration = flight_record.flight_duration
        duration_min = round(duration.total_seconds() / 60, 1) if duration is not None else None

        if duration_min is not None and duration_min < self._min_flight_duration_min:
            # Too short to be a real flight (a bounce/taxi blip). Drop the row and,
            # if it had consumed a claim, give the pilot their claim back so it
            # matches their actual next takeoff instead.
            claim = db.scalar(select(GliderClaim).where(GliderClaim.flight_id == flight.id))
            if claim is not None:
                claim.consumed_at = None
                claim.flight_id = None
            db.delete(flight)
            db.commit()
            return

        flight.landing_time = _aware(flight_record.landing_time)
        flight.duration_min = duration_min
        if flight_record.landing_airport is not None:
            flight.landing_airfield_icao = flight_record.landing_airport.icao
        else:
            # No known airfield within range - a candidate outlanding. Keep the raw
            # coordinates so a pilot can fill in the real place on correction,
            # instead of silently dropping the location.
            flight.landing_latitude = flight_record.latitude
            flight.landing_longitude = flight_record.longitude

        db.commit()

    def close(self) -> None:
        pass
