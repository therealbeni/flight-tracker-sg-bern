from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from db_sink import DbSink
from flight_record import FlightRecord
from models import Airport, FlightState  # tracker's own domain models

from shared.database import SessionLocal
from shared.models import Flight, Glider, GliderClaim, Pilot, PilotRole, PilotStatus

LSZB = Airport(icao="LSZB", name="Bern Belp", lat=46.9144, lon=7.4990, elevation_m=510.0)


def make_pilot(db, email="pilot@example.com") -> Pilot:
    pilot = Pilot(
        full_name="Test Pilot", email=email, password_hash="x", role=PilotRole.PILOT, status=PilotStatus.APPROVED
    )
    db.add(pilot)
    db.commit()
    db.refresh(pilot)
    return pilot


def make_glider(db, registration="HB-1811") -> Glider:
    glider = Glider(registration=registration, ogn_device_id="4B4BBA")
    db.add(glider)
    db.commit()
    db.refresh(glider)
    return glider


def make_claim(db, glider, pilot, expires_in_minutes=180) -> GliderClaim:
    now = datetime.now(timezone.utc)
    claim = GliderClaim(
        glider_id=glider.id, pilot_id=pilot.id, claimed_at=now, expires_at=now + timedelta(minutes=expires_in_minutes)
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


def fly(callsign, takeoff_at, landing_at, takeoff_airport=None, landing_airport=None):
    """Drive a FlightRecord through takeoff -> landing, the way GlobalFlightTracker
    would beacon by beacon. Yields once per event so the caller can log() each."""
    record = FlightRecord("ICA4B4BBA", "ASK-21", callsign, FlightState.GROUND, takeoff_at)
    record.update(46.91, 7.497, 540.0, 0.0, 0.0, takeoff_at)
    record.takeoff(takeoff_airport)
    yield record

    record.update(46.95, 7.55, 600.0, 90.0, 2.0, landing_at)
    record.land(landing_airport)
    yield record


def test_claimed_takeoff_assigns_pilot_and_consumes_claim(db_session):
    pilot = make_pilot(db_session)
    glider = make_glider(db_session)
    claim = make_claim(db_session, glider, pilot)

    sink = DbSink(SessionLocal)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(minutes=20)
    steps = fly("HB-1811", t0, t1, takeoff_airport=LSZB, landing_airport=LSZB)

    sink.log(next(steps))
    flight = db_session.scalar(select(Flight))
    assert flight is not None
    assert flight.pilot_id == pilot.id
    assert flight.takeoff_airfield_icao == "LSZB"

    db_session.refresh(claim)
    assert claim.consumed_at is not None
    assert claim.flight_id == flight.id

    sink.log(next(steps))
    db_session.refresh(flight)
    assert flight.landing_time is not None
    assert flight.landing_airfield_icao == "LSZB"
    assert flight.duration_min == pytest.approx(20.0, abs=0.1)


def test_unclaimed_takeoff_leaves_pilot_blank(db_session):
    make_glider(db_session)  # no claim created

    sink = DbSink(SessionLocal)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(minutes=15)
    steps = fly("HB-1811", t0, t1, takeoff_airport=LSZB, landing_airport=LSZB)

    sink.log(next(steps))
    flight = db_session.scalar(select(Flight))
    assert flight is not None
    assert flight.pilot_id is None


def test_landing_with_no_known_airfield_is_flagged_as_possible_outlanding(db_session):
    pilot = make_pilot(db_session)
    glider = make_glider(db_session)
    make_claim(db_session, glider, pilot)

    sink = DbSink(SessionLocal)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(minutes=45)
    steps = fly("HB-1811", t0, t1, takeoff_airport=LSZB, landing_airport=None)

    sink.log(next(steps))
    sink.log(next(steps))

    flight = db_session.scalar(select(Flight))
    assert flight.landing_airfield_icao is None
    assert flight.landing_latitude == pytest.approx(46.95)
    assert flight.landing_longitude == pytest.approx(7.55)
    assert flight.is_possible_outlanding


def test_too_short_flight_is_dropped_and_claim_is_returned(db_session):
    pilot = make_pilot(db_session)
    glider = make_glider(db_session)
    claim = make_claim(db_session, glider, pilot)

    sink = DbSink(SessionLocal)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(seconds=20)  # well under the 1 minute floor
    steps = fly("HB-1811", t0, t1, takeoff_airport=LSZB, landing_airport=LSZB)

    sink.log(next(steps))
    sink.log(next(steps))

    assert db_session.scalar(select(Flight)) is None
    db_session.refresh(claim)
    assert claim.consumed_at is None
    assert claim.flight_id is None


def test_non_club_registration_is_ignored(db_session):
    sink = DbSink(SessionLocal)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(minutes=10)
    steps = fly("D-ABCD", t0, t1, takeoff_airport=LSZB, landing_airport=LSZB)

    sink.log(next(steps))
    sink.log(next(steps))

    assert db_session.scalar(select(Flight)) is None


def test_expired_claim_is_not_matched(db_session):
    pilot = make_pilot(db_session)
    glider = make_glider(db_session)
    make_claim(db_session, glider, pilot, expires_in_minutes=-5)  # already expired

    sink = DbSink(SessionLocal)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(minutes=10)
    steps = fly("HB-1811", t0, t1, takeoff_airport=LSZB, landing_airport=LSZB)

    sink.log(next(steps))
    flight = db_session.scalar(select(Flight))
    assert flight.pilot_id is None
