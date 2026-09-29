"""Randomised stress test: many simulated flying days with realistic dirt.

Every simulated day is a sequence of flights separated by ground time. Beacons
then get GPS noise, altitude and speed spikes, random loss, duplicates,
out-of-order delivery and irregular intervals. For every seed the detector
must still find exactly the flights that happened, at the right airfield and
within a sensible time tolerance. Seeds are fixed, so failures are reproducible:
run a single one with `pytest -k "stress and 17"`.
"""

import random
from dataclasses import replace
from datetime import timedelta

import pytest

from sim import LSZB, Sim, landings, make_detector, run, takeoffs

SEEDS = range(150)


def dirty(beacons, rng: random.Random):
    out = []
    for b in beacons:
        if rng.random() < 0.3:
            continue  # lost
        alt_noise = rng.gauss(0, 6)
        if rng.random() < 0.03:
            alt_noise += rng.choice([-1, 1]) * rng.uniform(20, 45)  # GPS altitude spike
        speed = max(0.0, b.ground_speed_kmh + rng.gauss(0, 3))
        if b.ground_speed_kmh == 0 and rng.random() < 0.02:
            speed = rng.uniform(40, 80)  # GPS speed spike while parked
        delay = rng.expovariate(1 / 2.0)  # APRS network delay
        nb = replace(b, altitude_m=b.altitude_m + alt_noise, ground_speed_kmh=speed,
                     received_at=b.timestamp + timedelta(seconds=1 + delay))
        out.append(nb)
        if rng.random() < 0.1:  # duplicate via a second receiver, later
            out.append(replace(nb, received_at=nb.received_at + timedelta(seconds=rng.uniform(1, 40))))
    out.sort(key=lambda x: x.received_at)
    return out


def simulated_day(rng: random.Random):
    """Returns (beacons, list of (takeoff_time, landing_time))."""
    sim = Sim(interval=rng.choice([2.0, 4.0, 8.0])).park(rng.uniform(300, 1200))
    flights = []
    for _ in range(rng.randint(1, 6)):
        t_start = sim.t
        height = rng.uniform(250, 1500)
        sim.accelerate(0, rng.uniform(65, 90), rng.uniform(8, 20))
        sim.climb(height, speed=rng.uniform(100, 140), rate=rng.uniform(2, 6))
        sim.cruise(rng.uniform(0, 3600), speed=rng.uniform(80, 150))
        sim.fly_to(LSZB.lat + rng.uniform(-0.005, 0.005), LSZB.lon + rng.uniform(-0.005, 0.005))
        sim.descend(0, speed=rng.uniform(85, 110), rate=rng.uniform(2, 5))
        t_touchdown = sim.t
        sim.decelerate(rng.uniform(70, 90), 0, rng.uniform(15, 30))
        flights.append((t_start, t_touchdown))
        sim.park(rng.uniform(240, 1800))
    return sim.beacons, flights


@pytest.mark.parametrize("seed", SEEDS)
def test_stress_flying_day(seed):
    rng = random.Random(seed)
    beacons, expected = simulated_day(rng)
    events = run(make_detector(), dirty(beacons, rng))

    got_takeoffs, got_landings = takeoffs(events), landings(events)
    assert len(got_takeoffs) == len(expected), f"seed {seed}: takeoffs"
    assert len(got_landings) == len(expected), f"seed {seed}: landings"

    for flight, (t_start, t_touchdown) in zip(got_landings, expected):
        assert flight.takeoff_airport == LSZB and flight.landing_airport == LSZB
        assert not flight.takeoff_estimated and not flight.landing_estimated
        # Takeoff: speed crosses 60 km/h during the roll; beacon loss adds a bit.
        assert abs((flight.takeoff_time - t_start).total_seconds()) <= 45, f"seed {seed}: takeoff time"
        assert abs((flight.landing_time - t_touchdown).total_seconds()) <= 60, f"seed {seed}: landing time"


@pytest.mark.parametrize("seed", range(30))
def test_stress_parked_all_day_never_flies(seed):
    rng = random.Random(1000 + seed)
    sim = Sim(interval=rng.choice([2.0, 4.0, 10.0])).park(8 * 3600)
    assert run(make_detector(), dirty(sim.beacons, rng)) == []
