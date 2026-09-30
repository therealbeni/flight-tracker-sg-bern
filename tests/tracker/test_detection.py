"""Takeoff/landing detection, scenario by scenario.

Each test simulates a realistic beacon stream (see sim.py) for one situation
we have seen - or expect - in real OGN data, and checks that exactly the right
takeoffs and landings come out, with sensible times and airfields.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from detection import DetectionRules, FlightRecord
from sim import (
    FIELD, LSPG, LSZB, T0, Sim, clock, flat_terrain, landings, make_detector, run, shifted, takeoffs,
)


def approx_time(actual, expected, tolerance_s=30):
    assert abs((actual - expected).total_seconds()) <= tolerance_s, f"{actual} != {expected} (+-{tolerance_s}s)"


# --------------------------------------------------------------------- basics


def test_local_flight_gives_exactly_one_takeoff_and_one_landing():
    sim = Sim().park(300)
    roll_start = sim.t
    sim.local_flight(minutes=30)
    touchdown = sim.beacons[-7].timestamp  # start of the rollout
    sim.park(300)

    events = run(make_detector(), sim.beacons)

    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1
    flight = landings(events)[0]
    assert flight.takeoff_airport == LSZB and flight.landing_airport == LSZB
    assert not flight.takeoff_estimated and not flight.landing_estimated
    approx_time(flight.takeoff_time, roll_start, 20)
    approx_time(flight.landing_time, touchdown, 20)
    assert 28 <= flight.duration.total_seconds() / 60 <= 40


def test_takeoff_and_landing_are_the_same_record():
    sim = Sim().park(60).local_flight(minutes=15).park(120)
    events = run(make_detector(), sim.beacons)
    assert takeoffs(events)[0] is landings(events)[0]


def test_winch_launch():
    sim = Sim().park(120)
    sim.accelerate(0, 100, 4, climb_to=20).climb(to_height=400, speed=100, rate=15)
    sim.cruise(300).fly_to(LSZB.lat, LSZB.lon).descend(0).decelerate(90, 0, 20).park(120)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1


def test_short_circuit_of_three_minutes_is_a_real_flight():
    # Real data (2026-09-20): ASK-21 school circuits of ~3 minutes, all day.
    sim = Sim().park(120)
    for _ in range(5):
        sim.accelerate(0, 70, 12).climb(to_height=250, speed=110, rate=4)
        sim.fly_to(LSZB.lat, LSZB.lon, speed=100).descend(0, speed=95, rate=4).decelerate(90, 0, 20).park(600)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 5 and len(landings(events)) == 5
    for f in landings(events):
        assert 2 <= f.duration.total_seconds() / 60 <= 5


@pytest.mark.parametrize("seed", range(40))
def test_tight_motor_glider_circuit_with_dirty_beacons(seed):
    # Real case, 29.09.: HB-2377 (Dimona) flew a training circuit at LSZB,
    # 2.8 minutes from roll to touchdown, touching down at ~60 km/h. It must be
    # found even with lost, noisy and late beacons.
    import random
    from test_detection_stress import dirty

    rng = random.Random(seed)
    sim = Sim(interval=rng.choice([2.0, 4.0])).park(300)
    start = sim.t
    sim.accelerate(0, 90, 10).climb(to_height=150, speed=120, rate=4)
    sim.cruise(40, speed=140).fly_to(LSZB.lat, LSZB.lon, speed=130).descend(0, speed=100, rate=4)
    touchdown = sim.t
    sim.decelerate(60, 0, 20).park(600)
    events = run(make_detector(), dirty(sim.beacons, rng))
    [flight] = landings(events)
    assert flight.takeoff_airport == LSZB and flight.landing_airport == LSZB
    assert abs((flight.takeoff_time - start).total_seconds()) <= 30
    assert abs((flight.landing_time - touchdown).total_seconds()) <= 45
    assert (touchdown - start).total_seconds() / 60 < 4  # really that short


def test_cross_country_to_another_airfield():
    # Real data (2026-09-28): HB-2377 Bern -> Kaegiswil.
    sim = Sim().park(120).accelerate(0, 90, 15).climb(1500, speed=150, rate=3)
    sim.fly_to(LSPG.lat, LSPG.lon, speed=160).descend(0, speed=100).decelerate(90, 0, 20).park(300)
    flight = landings(run(make_detector(), sim.beacons))[0]
    assert flight.takeoff_airport == LSZB and flight.landing_airport == LSPG


# ------------------------------------------------------------- ground noise


def test_gps_altitude_spikes_while_parked_are_not_a_takeoff():
    # Real data: parked gliders at LSZB reported up to +28 m of altitude.
    sim = Sim().park(60)
    for i, b in enumerate(list(sim.beacons)):
        sim.beacons[i] = replace(b, altitude_m=b.altitude_m + (45 if i % 3 == 0 else -5))
    assert run(make_detector(), sim.beacons) == []


def test_gps_speed_spike_while_parked_is_not_a_takeoff():
    sim = Sim().park(60)
    spiked = sim.beacons[5]
    sim.beacons[5] = replace(spiked, ground_speed_kmh=75.0)
    sim.park(120)
    assert run(make_detector(), sim.beacons) == []


def test_car_towing_glider_along_the_runway_is_not_a_flight():
    sim = Sim().park(60).accelerate(0, 70, 10).cruise(90, speed=70, circle=False).decelerate(70, 0, 15).park(120)
    assert run(make_detector(), sim.beacons) == []


def test_trailer_on_the_motorway_is_not_a_flight():
    # Hilly road: altitude follows terrain, so height above ground stays ~0.
    sim = Sim().park(60).accelerate(0, 110, 20).cruise(3600, speed=110, circle=False).decelerate(110, 0, 30).park(300)
    assert run(make_detector(), sim.beacons) == []


# --------------------------------------------------- duplicates and ordering


def test_delayed_duplicate_beacons_after_landing_do_not_create_phantom_flights():
    # Real data (2026-09-26 13:33): HB-1811 got a "landing without takeoff"
    # 3 s after its real landing, from a late airborne beacon.
    sim = Sim().park(60).local_flight(minutes=20)
    airborne_tail = sim.beacons[-30:-20]
    sim.park(300)
    stream = list(sim.beacons)
    insert_at = len(stream) - 60
    for b in airborne_tail:
        stream.insert(insert_at, shifted(b, 60))
    events = run(make_detector(), stream)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1


def test_every_beacon_received_twice():
    sim = Sim().park(60).local_flight(minutes=20).park(120)
    doubled = [x for b in sim.beacons for x in (b, shifted(b, 2))]
    events = run(make_detector(), doubled)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1


def test_stale_beacon_is_ignored():
    sim = Sim().park(60)
    stale = replace(sim.beacons[-1], received_at=sim.beacons[-1].timestamp + timedelta(minutes=30))
    detector = make_detector()
    run(detector, sim.beacons[:-1])
    assert detector.process(stale) == []


# ----------------------------------------------------------- slow and low


def test_slow_low_flight_near_a_slope_is_not_a_landing():
    # Ridge soaring in strong wind: ground speed drops below 30 km/h while
    # terrain data says we're only ~30 m up, for ~15 s during a turn.
    sim = Sim(lat=FIELD[0], lon=FIELD[1], ground_alt=900).park(1)
    sim.beacons.clear()
    sim.height = 600
    sim.cruise(600, speed=90)
    sim.height = 30
    sim.cruise(16, speed=20, circle=False)
    sim.cruise(600, speed=90)
    detector = make_detector(terrain=flat_terrain(900))
    events = run(detector, sim.beacons)
    assert landings(events) == []


def test_approach_into_strong_headwind():
    # Ground speed well under 60 km/h on final: the landing must still be found.
    sim = Sim().park(60).accelerate(0, 70, 12).climb(500, speed=110).cruise(600)
    sim.fly_to(LSZB.lat, LSZB.lon, speed=45).descend(0, speed=40, rate=2).decelerate(35, 0, 10).park(300)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1


# ------------------------------------------------------------- outlandings


def test_outlanding_in_a_field():
    sim = Sim().park(60).accelerate(0, 70, 12).climb(1000, speed=110)
    sim.fly_to(*FIELD, speed=100).descend(0).decelerate(80, 0, 15)
    touchdown = sim.t
    sim.park(1800)
    flight = landings(run(make_detector(), sim.beacons))[0]
    assert flight.landing_airport is None
    assert flight.landing_latitude == pytest.approx(FIELD[0], abs=0.01)
    assert flight.landing_longitude == pytest.approx(FIELD[1], abs=0.01)
    assert not flight.landing_estimated
    approx_time(flight.landing_time, touchdown, 30)


# ------------------------------------------------------- losing the signal


def test_flarm_switched_off_right_after_touchdown():
    sim = Sim().park(60).local_flight(minutes=20)
    touchdown_region = sim.beacons[-6].timestamp
    del sim.beacons[-4:]  # only 2 rollout beacons, then silence
    events = run(make_detector(), sim.beacons)
    assert landings(events) == []  # not confirmed yet

    detector = make_detector()
    events = run(detector, sim.beacons)
    events += clock(detector, sim.beacons[-1].received_at, minutes=11)
    flight = landings(events)[0]
    assert flight.landing_airport == LSZB
    approx_time(flight.landing_time, touchdown_region, 20)


def test_signal_lost_low_on_final_closes_flight_as_estimated():
    sim = Sim().park(60).accelerate(0, 70, 12).climb(600, speed=110).cruise(600)
    sim.fly_to(LSZB.lat, LSZB.lon).descend(150)
    last_seen = sim.beacons[-1]
    detector = make_detector()
    run(detector, sim.beacons)
    assert clock(detector, last_seen.received_at, minutes=9) == []
    events = clock(detector, last_seen.received_at + timedelta(minutes=9), minutes=2)
    flight = landings(events)[0]
    assert flight.landing_estimated
    assert flight.landing_airport == LSZB
    assert flight.landing_time == last_seen.timestamp


def test_signal_lost_high_is_not_closed_early():
    sim = Sim().park(60).accelerate(0, 70, 12).climb(2000, speed=120, rate=5).cruise(300)
    last = sim.beacons[-1]
    detector = make_detector()
    run(detector, sim.beacons)
    assert clock(detector, last.received_at, minutes=299) == []
    flight = landings(clock(detector, last.received_at + timedelta(minutes=299), minutes=2))[0]
    assert flight.landing_estimated


def test_reappearing_after_long_silence_continues_the_same_flight():
    sim = Sim().park(60).accelerate(0, 70, 12).climb(2000, speed=120, rate=5).cruise(300)
    sim.silence(3600).cruise(600).fly_to(LSZB.lat, LSZB.lon).descend(0).decelerate(90, 0, 20).park(300)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1
    assert not landings(events)[0].landing_estimated


def test_reappearing_on_the_ground_after_silence_in_the_air():
    # Out of coverage from 1500 m, next seen standing at Kaegiswil 40 min later.
    sim = Sim().park(60).accelerate(0, 90, 15).climb(1500, speed=150).cruise(120)
    last_air = sim.beacons[-1]
    sim.silence(40 * 60).teleport(LSPG.lat, LSPG.lon, ground_alt=470).park(120)
    detector = make_detector(terrain=lambda lat, lon: 470 if lon > 8 else 510)
    events = run(detector, sim.beacons, sweep_every_s=1e9)  # no sweeps in between
    flight = landings(events)[0]
    assert flight.landing_estimated
    assert flight.landing_time == last_air.timestamp


def test_our_own_feed_outage_does_not_close_flights():
    sim = Sim().park(60).accelerate(0, 70, 12).climb(200, speed=110, rate=4).cruise(120)
    detector = make_detector()
    run(detector, sim.beacons)
    last = sim.beacons[-1].received_at
    # The feed was down for 30 minutes: the next sweep happens after it.
    assert detector.sweep(last + timedelta(minutes=30)) == []
    # Aircraft still silent 9 minutes after the feed came back: not yet.
    assert clock(detector, last + timedelta(minutes=30), minutes=9) == []
    # 10 minutes of silence *while we were receiving*: now it's closed.
    assert len(landings(clock(detector, last + timedelta(minutes=39), minutes=2))) == 1


# ------------------------------------------------- tracker (re)starts


def test_first_seen_in_the_air_gives_estimated_takeoff():
    sim = Sim()
    sim.height = 1200
    sim.cruise(600).fly_to(LSZB.lat, LSZB.lon).descend(0).decelerate(90, 0, 20).park(300)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1
    flight = landings(events)[0]
    assert flight.takeoff_estimated and flight.takeoff_airport is None
    assert flight.landing_airport == LSZB and not flight.landing_estimated


def test_first_seen_low_and_slow_is_not_an_estimated_takeoff():
    # e.g. rolling out after landing when the tracker starts.
    sim = Sim().decelerate(40, 0, 12).park(120)
    assert run(make_detector(), sim.beacons) == []


def test_restored_flight_lands_normally():
    detector = make_detector()
    record = FlightRecord(address="4B4BBA", registration="HB-1811", model="ASK-21",
                          takeoff_time=T0, takeoff_airport=LSZB)
    sim = Sim(start=T0 + timedelta(minutes=20))
    sim.height = 600
    sim.cruise(300).fly_to(LSZB.lat, LSZB.lon).descend(0).decelerate(90, 0, 20).park(120)
    detector.restore(record, now=sim.beacons[0].received_at)
    events = run(detector, sim.beacons)
    assert landings(events) == [record]
    assert not record.landing_estimated


def test_restored_flight_found_on_the_ground_is_estimated():
    detector = make_detector()
    record = FlightRecord(address="4B4BBA", registration="HB-1811", model="ASK-21",
                          takeoff_time=T0, takeoff_airport=LSZB)
    restart = T0 + timedelta(minutes=45)
    detector.restore(record, now=restart)
    sim = Sim(start=restart + timedelta(minutes=2)).park(120)
    events = run(detector, sim.beacons)
    assert landings(events) == [record]
    assert record.landing_estimated and record.landing_time == restart
    assert record.landing_airport == LSZB


def test_restored_flight_never_heard_of_again_is_closed_eventually():
    detector = make_detector()
    record = FlightRecord(address="4B4BBA", registration="HB-1811", model="ASK-21",
                          takeoff_time=T0, takeoff_airport=LSZB)
    detector.restore(record, now=T0 + timedelta(hours=1))
    assert clock(detector, T0 + timedelta(hours=1), minutes=299) == []
    events = clock(detector, T0 + timedelta(hours=1, minutes=299), minutes=2)
    assert landings(events) == [record] and record.landing_estimated


# ------------------------------------------------------------ edge cases


def test_touch_and_go_is_one_flight():
    sim = Sim().park(60).local_flight(minutes=15)
    sim.beacons = sim.beacons[:-4]  # touch down, roll briefly ...
    sim.speed = 60
    sim.accelerate(60, 90, 8).climb(300, speed=110)  # ... and go again
    sim.fly_to(LSZB.lat, LSZB.lon).descend(0).decelerate(90, 0, 20).park(120)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1


def test_tow_plane_back_to_back_tows():
    sim = Sim(address="3D0EB4").park(60)
    for _ in range(4):
        sim.local_flight(minutes=10, height=500).park(180)
    events = run(make_detector(), sim.beacons)
    assert len(takeoffs(events)) == 4 and len(landings(events)) == 4


def test_two_aircraft_in_parallel_are_independent():
    glider = Sim(address="4B4BBA").park(60).local_flight(minutes=40).park(60)
    tug = Sim(address="3D0EB4").park(60).local_flight(minutes=12, height=500).park(60)
    stream = sorted(glider.beacons + tug.beacons, key=lambda b: b.received_at)
    events = run(make_detector(), stream)
    assert sorted(f.address for f in landings(events)) == ["3D0EB4", "4B4BBA"]


def test_parked_then_seen_again_already_in_the_air():
    # Receiver doesn't cover the takeoff: last seen parked at Bern, next at 600 m.
    sim = Sim().park(120).silence(1200)
    sim.height = 600
    sim.cruise(600)
    flight = takeoffs(run(make_detector(), sim.beacons))[0]
    assert flight.takeoff_estimated and flight.takeoff_airport == LSZB


def test_works_without_terrain_data():
    sim = Sim().park(120).local_flight(minutes=20).park(120)
    events = run(make_detector(terrain=lambda lat, lon: None), sim.beacons)
    assert len(takeoffs(events)) == 1 and len(landings(events)) == 1


def test_idle_aircraft_are_forgotten():
    detector = make_detector()
    run(detector, Sim().park(60).beacons)
    assert detector._tracks
    clock(detector, T0, minutes=13 * 60, step_s=60)
    assert not detector._tracks


def test_thresholds_are_configurable():
    strict = DetectionRules(takeoff_confirm_height_m=1000)
    sim = Sim().park(60).local_flight(minutes=20, height=800).park(120)
    assert run(make_detector(rules=strict), sim.beacons) == []
