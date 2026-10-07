"""Flight tracks: positions saved while in the air, packed at the landing."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from db_sink import DbSink
from flight_tracker import Tracker
from shared import tracks
from shared.database import SessionLocal
from shared.models import Airfield, Flight, FlightTrack, Glider, TrackPoint
from sim import Sim, make_detector
from track_recorder import TrackRecorder, pack_landed


@pytest.fixture
def glider(db_session) -> Glider:
    db_session.add(Airfield(icao="LSZB", name="Bern Belp", latitude=46.9144, longitude=7.4990, elevation_m=510.0))
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    return glider


def make_tracker():
    detector = make_detector()
    recorder = TrackRecorder(SessionLocal, detector.open_flight, lambda lat, lon: 510.0, flush_every_s=0)
    return Tracker(detector, [DbSink(SessionLocal), recorder], observers=[recorder]), recorder


def test_track_is_saved_live_and_packed_at_landing(db_session, glider):
    sim = Sim().park(120)
    roll_start = sim.t
    sim.accelerate(0, 70, 16).climb(to_height=600, speed=120)
    tracker, _ = make_tracker()
    for beacon in sim.beacons:
        tracker.process(beacon)
    flight = db_session.scalar(select(Flight))
    live = tracks.live_points(db_session, flight.id)
    # From the ground roll on, though the takeoff was only reported 50 m up.
    assert live[0].time <= roll_start + timedelta(seconds=8) and live[-1].time == sim.beacons[-1].timestamp
    assert live[-1].height_m == pytest.approx(600)

    sim.beacons.clear()
    sim.cruise(600).descend(to_height=0).decelerate(90, 0, 24).park(120)
    for beacon in sim.beacons:
        tracker.process(beacon)
    db_session.expire_all()
    assert db_session.scalar(select(TrackPoint.id)) is None  # packed
    packed = db_session.get(FlightTrack, flight.id)
    points = tracks.unpack(packed.data)
    assert packed.point_count == len(points) > 200
    assert points[0].time == live[0].time and all(a.time < b.time for a, b in zip(points, points[1:]))
    assert points[-1].latitude == pytest.approx(packed.last_latitude, abs=1e-5)
    assert len(packed.data) < 12 * len(points)  # compact


def test_too_short_flight_leaves_no_track(db_session, glider):
    sim = Sim().park(60).accelerate(0, 70, 16).climb(to_height=60, speed=100).descend(to_height=0, rate=4)
    sim.decelerate(80, 0, 20).park(120)
    tracker, _ = make_tracker()
    for beacon in sim.beacons:
        tracker.process(beacon)
    assert db_session.scalar(select(Flight.id)) is None
    assert db_session.scalar(select(TrackPoint.id)) is None and db_session.scalar(select(FlightTrack.flight_id)) is None


def test_pack_round_trip():
    from datetime import datetime, timezone
    t0 = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
    points = [tracks.Point(t0 + timedelta(seconds=4 * i), 46.91 + i * 1e-4, 7.49, 510.0 + i, 505.0 if i else None,
                           90.0, -1.2 if i else None) for i in range(5)]
    back = tracks.unpack(tracks.pack(points))
    assert back[3].time == points[3].time and back[3].latitude == pytest.approx(points[3].latitude, abs=1e-5)
    assert (back[0].ground_m, back[0].climb_ms, back[1].climb_ms) == (None, None, -1.2)


def test_leftover_points_of_landed_flights_are_packed_at_startup(db_session, glider):
    from datetime import datetime, timezone
    t0 = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
    flight = Flight(record_id="r1", glider_id=glider.id, takeoff_time=t0, landing_time=t0 + timedelta(minutes=5))
    db_session.add(flight)
    db_session.commit()
    tracks.store(db_session, flight.id, [tracks.Point(t0 + timedelta(seconds=i), 46.9, 7.5, 600, 510, 90, 1)
                                         for i in range(3)])
    db_session.commit()
    assert pack_landed(SessionLocal) == 1
    db_session.expire_all()
    assert db_session.get(FlightTrack, flight.id).point_count == 3
