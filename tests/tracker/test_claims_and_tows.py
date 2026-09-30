"""Which pilot a takeoff gets (check-ins), and pairing gliders with tow planes."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from db_sink import DbSink
from detection import EventKind, FlightEvent, FlightRecord
from shared.database import SessionLocal
from shared.models import (AircraftKind, Airfield, Flight, Glider, GliderClaim, LaunchMethod, Pilot, PilotRole,
                           PilotStatus)
from sim import LSPG, LSZB

T = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def fleet(db_session):
    db_session.add(Airfield(icao="LSZB", name="Bern", latitude=46.91, longitude=7.50, elevation_m=510))
    aircraft = {
        "glider": Glider(registration="HB-1811", ogn_device_id="4B4BBA", kind=AircraftKind.GLIDER),
        "glider2": Glider(registration="HB-3131", ogn_device_id="4B50E2", kind=AircraftKind.GLIDER),
        "tow": Glider(registration="D-EDUY", ogn_device_id="3D0EB4", kind=AircraftKind.TOWPLANE),
        "motor": Glider(registration="HB-2377", ogn_device_id="4B4DF0", kind=AircraftKind.MOTORGLIDER),
    }
    db_session.add_all(aircraft.values())
    db_session.commit()
    return aircraft


def pilot(db, name):
    p = Pilot(full_name=name, email=f"{name}@example.com", password_hash="x", role=PilotRole.PILOT,
              status=PilotStatus.APPROVED)
    db.add(p)
    db.commit()
    return p


def claim(db, glider, who, at, whole_day=False, hours=10):
    c = GliderClaim(glider_id=glider.id, pilot_id=who.id, claimed_at=at, expires_at=at + timedelta(hours=hours),
                    whole_day=whole_day)
    db.add(c)
    db.commit()
    return c


def fly(sink, glider, takeoff, minutes=20, airport=LSZB, estimated=False):
    record = FlightRecord(address=glider.ogn_device_id, registration=glider.registration, model="",
                          takeoff_time=takeoff, takeoff_airport=airport, takeoff_estimated=estimated)
    sink.handle(FlightEvent(EventKind.TAKEOFF, record))
    record.landing_time = takeoff + timedelta(minutes=minutes)
    record.landing_airport = airport
    sink.handle(FlightEvent(EventKind.LANDING, record))
    return record.record_id


def row(db, record_id) -> Flight:
    db.expire_all()
    return db.scalar(select(Flight).where(Flight.record_id == record_id))


def test_whole_day_claim_gets_every_takeoff(db_session, fleet):
    tow_pilot = pilot(db_session, "Toni")
    claim(db_session, fleet["tow"], tow_pilot, T - timedelta(hours=1), whole_day=True)
    sink = DbSink(SessionLocal)
    tows = [fly(sink, fleet["tow"], T + timedelta(minutes=30 * i), minutes=10) for i in range(4)]
    assert [row(db_session, r).pilot_id for r in tows] == [tow_pilot.id] * 4


def test_single_claim_on_top_of_a_day_claim_wins_once(db_session, fleet):
    toni, bea = pilot(db_session, "Toni"), pilot(db_session, "Bea")
    claim(db_session, fleet["tow"], toni, T - timedelta(hours=2), whole_day=True)
    claim(db_session, fleet["tow"], bea, T - timedelta(minutes=5))
    sink = DbSink(SessionLocal)
    first = fly(sink, fleet["tow"], T, minutes=10)
    second = fly(sink, fleet["tow"], T + timedelta(minutes=30), minutes=10)
    assert row(db_session, first).pilot_id == bea.id
    assert row(db_session, second).pilot_id == toni.id


def test_cancelled_claim_is_not_used(db_session, fleet):
    toni = pilot(db_session, "Toni")
    c = claim(db_session, fleet["glider"], toni, T - timedelta(minutes=10))
    c.cancelled_at = T - timedelta(minutes=5)
    db_session.commit()
    assert row(db_session, fly(DbSink(SessionLocal), fleet["glider"], T)).pilot_id is None


def test_claim_made_after_the_takeoff_is_not_used_for_it(db_session, fleet):
    toni = pilot(db_session, "Toni")
    claim(db_session, fleet["glider"], toni, T + timedelta(minutes=5))
    record_id = fly(DbSink(SessionLocal), fleet["glider"], T)
    assert row(db_session, record_id).pilot_id is None


def test_single_claim_is_used_up(db_session, fleet):
    toni = pilot(db_session, "Toni")
    claim(db_session, fleet["glider"], toni, T - timedelta(minutes=10))
    sink = DbSink(SessionLocal)
    first = fly(sink, fleet["glider"], T)
    second = fly(sink, fleet["glider"], T + timedelta(hours=1))
    assert row(db_session, first).pilot_id == toni.id
    assert row(db_session, second).pilot_id is None


@pytest.mark.parametrize("tow_detected_first", [True, False])
def test_glider_is_linked_to_its_tow_plane(db_session, fleet, tow_detected_first):
    toni = pilot(db_session, "Toni")
    claim(db_session, fleet["tow"], toni, T - timedelta(hours=1), whole_day=True)
    sink = DbSink(SessionLocal)
    if tow_detected_first:
        tow = fly(sink, fleet["tow"], T + timedelta(seconds=4), minutes=9)
        glider = fly(sink, fleet["glider"], T, minutes=45)
    else:
        glider = fly(sink, fleet["glider"], T, minutes=45)
        tow = fly(sink, fleet["tow"], T + timedelta(seconds=4), minutes=9)
    g = row(db_session, glider)
    assert g.launch_method is LaunchMethod.AEROTOW
    assert g.tow_flight.record_id == tow
    assert g.tow_flight.pilot_id == toni.id
    assert g.tow_flight.duration_min == pytest.approx(9)
    assert g.tow_glider_id == fleet["tow"].id
    t = row(db_session, tow)
    assert t.launch_method is LaunchMethod.SELF
    assert (t.flight_type, t.billing) == ("F", "none")  # Vereinsflieger: billed with the towed glider
    assert (g.flight_type, g.billing) == ("N", "pilot")


def test_two_gliders_towed_one_after_another(db_session, fleet):
    sink = DbSink(SessionLocal)
    g1 = fly(sink, fleet["glider"], T, minutes=60)
    t1 = fly(sink, fleet["tow"], T + timedelta(seconds=3), minutes=8)
    t2 = fly(sink, fleet["tow"], T + timedelta(minutes=12), minutes=8)
    g2 = fly(sink, fleet["glider2"], T + timedelta(minutes=12, seconds=2), minutes=60)
    assert row(db_session, g1).tow_flight.record_id == t1
    assert row(db_session, g2).tow_flight.record_id == t2


def test_no_link_when_takeoffs_are_far_apart_or_elsewhere(db_session, fleet):
    sink = DbSink(SessionLocal)
    fly(sink, fleet["tow"], T, minutes=8)
    late = fly(sink, fleet["glider"], T + timedelta(minutes=3))
    other_field = fly(sink, fleet["glider2"], T + timedelta(seconds=5), airport=LSPG)
    assert row(db_session, late).tow_flight_id is None
    assert row(db_session, other_field).tow_flight_id is None


def test_estimated_takeoff_is_not_linked(db_session, fleet):
    sink = DbSink(SessionLocal)
    fly(sink, fleet["tow"], T, minutes=8)
    assert row(db_session, fly(sink, fleet["glider"], T, estimated=True)).tow_flight_id is None


def test_motor_glider_is_self_launched(db_session, fleet):
    record_id = fly(DbSink(SessionLocal), fleet["motor"], T)
    assert row(db_session, record_id).launch_method is LaunchMethod.SELF


def test_dropping_a_too_short_tow_flight_unlinks_the_glider(db_session, fleet):
    sink = DbSink(SessionLocal)
    glider = fly(sink, fleet["glider"], T, minutes=50)
    fly(sink, fleet["tow"], T + timedelta(seconds=2), minutes=0.4)  # aborted takeoff
    g = row(db_session, glider)
    assert g.tow_flight_id is None and g.tow_glider_id is None and g.launch_method is None


def test_deleted_tow_flight_is_not_linked(db_session, fleet):
    sink = DbSink(SessionLocal)
    tow = fly(sink, fleet["tow"], T, minutes=8)
    row(db_session, tow).deleted_at = T
    db_session.commit()
    assert row(db_session, fly(sink, fleet["glider"], T)).tow_flight_id is None
