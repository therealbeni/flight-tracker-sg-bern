"""A tiny flight simulator that produces OGN-like beacon streams for tests.

Build a track out of segments (parked, rolling, climbing, cruising, ...),
optionally add noise/duplicates/gaps, then feed it to a FlightDetector:

    sim = Sim(start=T0, lat=LSZB.lat, lon=LSZB.lon, ground_alt=510)
    sim.park(300).accelerate(0, 100, 15).climb(to_height=600, speed=110)
    sim.cruise(1800).descend(to_height=0, speed=95).decelerate(95, 0, 20).park(300)
    events = run(detector, sim.beacons)
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from airports import Airport, AirportDirectory
from detection import Beacon, DetectionRules, EventKind, FlightDetector

T0 = datetime(2026, 9, 26, 9, 0, 0, tzinfo=timezone.utc)

LSZB = Airport("LSZB", "Bern Airport", 46.9144, 7.4990, 510.0)
LSPG = Airport("LSPG", "Kägiswil Airfield", 46.9081, 8.2542, 470.0)
LSTZ = Airport("LSTZ", "Zweisimmen Airfield", 46.5517, 7.3810, 935.0)
AIRPORTS = AirportDirectory([LSZB, LSPG, LSTZ], radius_km=3.0)

# A field in the Emmental ~15 km from any airfield, for outlandings.
FIELD = (46.95, 7.70)

METERS_PER_DEG_LAT = 111_320.0


def flat_terrain(elevation: float = 510.0):
    return lambda lat, lon: elevation


def make_detector(terrain=None, rules: Optional[DetectionRules] = None) -> FlightDetector:
    return FlightDetector(
        terrain=terrain or flat_terrain(),
        nearest_airport=AIRPORTS.nearest,
        aircraft_info=lambda address: ("HB-1811", "ASK-21"),
        rules=rules,
    )


class Sim:
    def __init__(self, start: datetime = T0, lat: float = LSZB.lat, lon: float = LSZB.lon,
                 ground_alt: float = 510.0, address: str = "4B4BBA", interval: float = 4.0):
        self.t = start
        self.lat, self.lon = lat, lon
        self.ground_alt = ground_alt
        self.height = 0.0
        self.speed = 0.0
        self.heading = 90.0  # degrees, east
        self.address = address
        self.interval = interval
        self.beacons: list[Beacon] = []

    # --- primitives

    def _emit(self, climb: float = 0.0, altitude_offset: float = 0.0) -> None:
        self.beacons.append(Beacon(
            address=self.address,
            timestamp=self.t,
            received_at=self.t + timedelta(seconds=1),
            latitude=self.lat,
            longitude=self.lon,
            altitude_m=self.ground_alt + self.height + altitude_offset,
            ground_speed_kmh=self.speed,
            climb_rate_ms=climb,
            name=f"ICA{self.address}",
        ))

    def _advance(self, seconds: float) -> None:
        distance = self.speed / 3.6 * seconds
        self.lat += distance * math.cos(math.radians(self.heading)) / METERS_PER_DEG_LAT
        self.lon += distance * math.sin(math.radians(self.heading)) / (METERS_PER_DEG_LAT * math.cos(math.radians(self.lat)))
        self.t += timedelta(seconds=seconds)

    def _steps(self, duration: float):
        n = max(1, int(duration / self.interval))
        for _ in range(n):
            yield self.interval

    # --- segments

    def park(self, duration: float) -> "Sim":
        self.speed, self.height = 0.0, 0.0
        for dt in self._steps(duration):
            self._advance(dt)
            self._emit()
        return self

    def accelerate(self, v_from: float, v_to: float, duration: float, climb_to: float = 0.0) -> "Sim":
        steps = list(self._steps(duration))
        for i, dt in enumerate(steps, 1):
            self.speed = v_from + (v_to - v_from) * i / len(steps)
            if climb_to:
                self.height = climb_to * i / len(steps)
            self._advance(dt)
            self._emit()
        return self

    def decelerate(self, v_from: float, v_to: float, duration: float) -> "Sim":
        self.height = 0.0
        return self.accelerate(v_from, v_to, duration)

    def climb(self, to_height: float, speed: float = 110.0, rate: float = 3.0) -> "Sim":
        self.speed = speed
        while self.height < to_height:
            self.height = min(to_height, self.height + rate * self.interval)
            self._advance(self.interval)
            self._emit(climb=rate)
        return self

    def descend(self, to_height: float, speed: float = 95.0, rate: float = 3.0) -> "Sim":
        self.speed = speed
        while self.height > to_height:
            self.height = max(to_height, self.height - rate * self.interval)
            self.heading = (self.heading + 360 * self.interval / 120) % 360  # circle overhead
            self._advance(self.interval)
            self._emit(climb=-rate)
        return self

    def cruise(self, duration: float, speed: float = 100.0, circle: bool = True) -> "Sim":
        """Fly around, circling back so we end up roughly where we started."""
        self.speed = speed
        for dt in self._steps(duration):
            if circle:
                self.heading = (self.heading + 360 * dt / 600) % 360  # one lap per 10 min
            self._advance(dt)
            self._emit()
        return self

    def fly_to(self, lat: float, lon: float, speed: float = 120.0) -> "Sim":
        self.speed = speed
        dy = (lat - self.lat) * METERS_PER_DEG_LAT
        dx = (lon - self.lon) * METERS_PER_DEG_LAT * math.cos(math.radians(self.lat))
        distance = math.hypot(dx, dy)
        seconds = distance / (speed / 3.6)
        steps = max(1, int(seconds / self.interval))
        lat0, lon0 = self.lat, self.lon
        for i in range(1, steps + 1):
            self.lat = lat0 + (lat - lat0) * i / steps
            self.lon = lon0 + (lon - lon0) * i / steps
            self.t += timedelta(seconds=self.interval)
            self._emit()
        return self

    def silence(self, seconds: float) -> "Sim":
        """Nothing received for a while (the aircraft keeps its state)."""
        self.t += timedelta(seconds=seconds)
        return self

    def teleport(self, lat: float, lon: float, ground_alt: Optional[float] = None) -> "Sim":
        self.lat, self.lon = lat, lon
        if ground_alt is not None:
            self.ground_alt = ground_alt
        return self

    # --- standard flights

    def local_flight(self, minutes: float = 30, height: float = 800) -> "Sim":
        """Aerotow launch, soaring, circuit and landing back where we started."""
        start_lat, start_lon = self.lat, self.lon
        self.accelerate(0, 70, 16).climb(to_height=height, speed=120)
        self.cruise(minutes * 60 - 16 - height / 3 - height / 3)
        self.fly_to(start_lat, start_lon, speed=100)
        self.descend(to_height=0, speed=95)
        self.decelerate(90, 0, 24)
        return self


# --- helpers for tests


def run(detector: FlightDetector, beacons: Iterable[Beacon], sweep_every_s: float = 30.0):
    """Feed beacons in order, sweeping on the stream clock like run.py does."""
    events = []
    last_sweep = None
    for b in beacons:
        events += detector.process(b)
        if last_sweep is None or (b.received_at - last_sweep).total_seconds() >= sweep_every_s:
            events += detector.sweep(b.received_at)
            last_sweep = b.received_at
    return events


def clock(detector: FlightDetector, start, minutes: float, step_s: float = 30.0):
    """Let stream time pass with the feed up (other aircraft keep it ticking),
    sweeping every `step_s` like production does. Returns the events."""
    events = []
    t = start
    end = start + timedelta(minutes=minutes)
    while t < end:
        t = min(end, t + timedelta(seconds=step_s))
        events += detector.sweep(t)
    return events


def takeoffs(events):
    return [e.flight for e in events if e.kind is EventKind.TAKEOFF]


def landings(events):
    return [e.flight for e in events if e.kind is EventKind.LANDING]


def shifted(b: Beacon, seconds: float) -> Beacon:
    """Same beacon, received `seconds` later (a delayed duplicate)."""
    return replace(b, received_at=b.received_at + timedelta(seconds=seconds))
