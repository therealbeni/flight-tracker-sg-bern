"""DbSink: detector events -> rows in the web app's `flights` table."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from airports import Airport
from db_sink import DbSink
from detection import EventKind, FlightEvent, FlightRecord
from flight_tracker import Tracker
from shared.database import SessionLocal
from shared.models import Airfield, Flight, FlightSource, Glider, GliderClaim, Pilot, PilotRole, PilotStatus
from sim import LSPG, LSZB, Sim, make_detector

@pytest.fixture
def glider(db_session) -> Glider:
    db_session.add(Airfield(icao="LSZB", name="Bern Belp", latitude=46.9144, longitude=7.4990, elevation_m=510.0))
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    return glider


@pytest.fixture
def pilot(db_session) -> Pilot:
    pilot = Pilot(full_name="Test Pilot", email="pilot@example.com", password_hash="x",
                  role=PilotRole.PILOT, status=PilotStatus.APPROVED)
    db_session.add(pilot)
    db_session.commit()
    return pilot


def make_claim(db, glider, pilot, expires_in_minutes=180, whole_day=False, at=None) -> GliderClaim:
    now = at or datetime.now(timezone.utc) - timedelta(minutes=1)
    claim = GliderClaim(glider_id=glider.id, pilot_id=pilot.id, claimed_at=now,
                        expires_at=now + timedelta(minutes=expires_in_minutes), whole_day=whole_day)
    db.add(claim)
    db.commit()
    return claim


def flight(minutes=20.0, address="4B4BBA", registration="HB-1811", landing_airport=LSZB, **landing):
    """A takeoff event and the matching landing event, as the detector emits them."""
    t0 = datetime.now(timezone.utc)
    record = FlightRecord(address=address, registration=registration, model="ASK-21",
                          takeoff_time=t0, takeoff_airport=LSZB)
    takeoff = FlightEvent(EventKind.TAKEOFF, replace(record))
    landed = replace(record, landing_time=t0 + timedelta(minutes=minutes), landing_airport=landing_airport,
                     landing_latitude=46.95, landing_longitude=7.55, **landing)
    return takeoff, FlightEvent(EventKind.LANDING, landed)


def test_claimed_takeoff_assigns_pilot_and_consumes_claim(db_session, glider, pilot):
    claim = make_claim(db_session, glider, pilot)
    sink = DbSink(SessionLocal)
    takeoff, landing = flight(minutes=20)

    sink.handle(takeoff)
    row = db_session.scalar(select(Flight))
    assert row.pilot_id == pilot.id
    assert row.takeoff_airfield_icao == "LSZB"
    assert row.source is FlightSource.AUTO
    db_session.refresh(claim)
    assert claim.consumed_at is not None and claim.flight_id == row.id

    sink.handle(landing)
    db_session.refresh(row)
    assert row.landing_airfield_icao == "LSZB"
    assert row.duration_min == pytest.approx(20.0)
    assert not row.takeoff_estimated and not row.landing_estimated


def test_unclaimed_takeoff_leaves_pilot_blank(db_session, glider):
    DbSink(SessionLocal).handle(flight()[0])
    assert db_session.scalar(select(Flight)).pilot_id is None


def test_expired_claim_is_not_matched(db_session, glider, pilot):
    make_claim(db_session, glider, pilot, expires_in_minutes=-5)
    DbSink(SessionLocal).handle(flight()[0])
    assert db_session.scalar(select(Flight)).pilot_id is None


def test_outlanding_keeps_coordinates(db_session, glider):
    sink = DbSink(SessionLocal)
    for event in flight(minutes=45, landing_airport=None):
        sink.handle(event)
    row = db_session.scalar(select(Flight))
    assert row.landing_airfield_icao is None
    assert (row.landing_latitude, row.landing_longitude) == (pytest.approx(46.95), pytest.approx(7.55))
    assert row.is_possible_outlanding


def test_landing_at_unknown_airfield_adds_it(db_session, glider):
    # Real case, 28.09.: HB-2377 flew to Kägiswil, which wasn't in the table,
    # and the foreign key would have rejected the flight.
    sink = DbSink(SessionLocal)
    for event in flight(minutes=30, landing_airport=LSPG):
        sink.handle(event)
    assert db_session.scalar(select(Flight)).landing_airfield_icao == "LSPG"
    assert db_session.get(Airfield, "LSPG").name == LSPG.name


def test_airfield_without_icao_code_fits(db_session, glider):
    field = Airport("CH-0012", "Some Farm Strip", 46.9, 7.6, 600.0)
    sink = DbSink(SessionLocal)
    for event in flight(landing_airport=field):
        sink.handle(event)
    assert db_session.scalar(select(Flight)).landing_airfield_icao == "CH-0012"


def test_too_short_flight_is_dropped_and_claim_is_returned(db_session, glider, pilot):
    claim = make_claim(db_session, glider, pilot)
    sink = DbSink(SessionLocal)
    for event in flight(minutes=0.3):
        sink.handle(event)
    assert db_session.scalar(select(Flight)) is None
    db_session.refresh(claim)
    assert claim.consumed_at is None and claim.flight_id is None


def test_short_but_estimated_flight_is_kept(db_session, glider):
    # Signal lost right after takeoff: the flight happened, we just don't know
    # how long it was. Keep it so the pilot can correct it.
    sink = DbSink(SessionLocal)
    for event in flight(minutes=0.3, landing_estimated=True):
        sink.handle(event)
    row = db_session.scalar(select(Flight))
    assert row is not None and row.landing_estimated


def test_non_club_aircraft_is_ignored(db_session, glider):
    sink = DbSink(SessionLocal)
    for event in flight(address="3E1234", registration="D-ABCD"):
        sink.handle(event)
    assert db_session.scalar(select(Flight)) is None


def test_matched_by_device_address_without_registration(db_session, glider):
    # OGN device database download failed: no registration known.
    sink = DbSink(SessionLocal)
    sink.handle(flight(registration="")[0])
    assert db_session.scalar(select(Flight)).glider_id == glider.id


def test_inactive_glider_is_ignored(db_session, glider):
    glider.active = False
    db_session.commit()
    DbSink(SessionLocal).handle(flight()[0])
    assert db_session.scalar(select(Flight)) is None


def test_landing_without_stored_takeoff_creates_the_flight(db_session, glider):
    # The database was down when the takeoff happened.
    DbSink(SessionLocal).handle(flight(minutes=25)[1])
    row = db_session.scalar(select(Flight))
    assert row.takeoff_time is not None and row.landing_time is not None
    assert row.duration_min == pytest.approx(25.0)


def test_takeoff_twice_does_not_duplicate(db_session, glider):
    sink = DbSink(SessionLocal)
    takeoff, _ = flight()
    sink.handle(takeoff)
    sink.handle(takeoff)
    assert len(db_session.scalars(select(Flight)).all()) == 1


def test_open_flights_are_restored_after_restart(db_session, glider):
    sink = DbSink(SessionLocal)
    takeoff, _ = flight()
    sink.handle(takeoff)
    restored = sink.open_flights(timedelta(hours=5), {"LSZB": LSZB}.get)
    assert [(f.record_id, f.address, f.registration) for f in restored] == [
        (takeoff.flight.record_id, "4B4BBA", "HB-1811")]
    assert restored[0].takeoff_airport == LSZB


def test_old_open_flights_are_not_restored(db_session, glider):
    sink = DbSink(SessionLocal)
    takeoff, _ = flight()
    takeoff.flight.takeoff_time -= timedelta(hours=6)
    sink.handle(takeoff)
    assert sink.open_flights(timedelta(hours=5), {"LSZB": LSZB}.get) == []


def test_fleet_comes_from_the_gliders_table(db_session, glider):
    db_session.add(Glider(registration="HB-9999", ogn_device_id="abc123", active=False))
    db_session.commit()
    assert DbSink(SessionLocal).fleet() == {"4B4BBA": "HB-1811"}


def test_end_to_end_simulated_flight_lands_in_the_database(db_session, glider, pilot):
    sim = Sim().park(300)
    make_claim(db_session, glider, pilot, at=sim.t)
    takeoff_roll = sim.t
    sim.local_flight(minutes=25)
    touchdown = sim.t - timedelta(seconds=24)  # local_flight ends with a 24 s rollout
    sim.park(300)
    tracker = Tracker(make_detector(), [DbSink(SessionLocal)])
    for beacon in sim.beacons:
        tracker.process(beacon)
    rows = db_session.scalars(select(Flight)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.pilot_id == pilot.id
    assert (row.takeoff_airfield_icao, row.landing_airfield_icao) == ("LSZB", "LSZB")
    assert row.duration_min == pytest.approx((touchdown - takeoff_roll).total_seconds() / 60, abs=0.5)
