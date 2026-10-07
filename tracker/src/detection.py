"""Takeoff and landing detection from OGN position beacons.

This is the heart of the tracker. It turns a stream of position reports
("beacons") into flights, and has to cope with everything real OGN data throws
at it:

* **Noise.** GPS altitude on the ground jumps by 30 m and more, ground speed
  flickers, terrain data (SRTM, ~30-90 m grid) is imperfect in the mountains.
  A single beacon is therefore never enough to change state: every takeoff and
  landing must be *confirmed* by several beacons (see DetectionRules).
* **Hysteresis.** "Clearly flying" and "clearly on the ground" use different
  thresholds with a gap in between. A beacon in the gap ("unsure", e.g. a
  glider rolling out at 45 km/h) never changes state on its own.
* **Out-of-order and duplicate beacons.** The same transmission can arrive via
  several receivers and with delays. Beacons older than the last one we used
  for that aircraft are ignored. (The previous implementation created phantom
  "landings without takeoff" from exactly this.)
* **Coverage gaps.** Receivers often lose aircraft close to the ground, and
  pilots switch FLARM off right after landing. When an airborne aircraft goes
  silent, sweep() eventually closes the flight with an *estimated* landing.
  Silence caused by our own feed being down is not counted.
* **Things that move but don't fly.** Cars towing gliders along the runway,
  trailers on the motorway. A takeoff is only confirmed once the aircraft has
  actually climbed away from the ground (DetectionRules.takeoff_confirm_height_m).

Times are always timezone-aware UTC datetimes.

The detector is pure logic: no I/O except the injected terrain and airport
lookups, so it can be tested beacon by beacon (tests/tracker/test_detection.py).
"""

from __future__ import annotations

import enum
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from airports import Airport


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DetectionRules:
    """All tunable thresholds in one place. Defaults are chosen for gliders,
    motor gliders and tow planes; see docs/how-it-works.md for the reasoning."""

    # A beacon is "clearly flying" at or above this ground speed ...
    air_speed_kmh: float = 60.0
    # ... or at or above this height above ground (regardless of speed).
    air_height_m: float = 100.0

    # A beacon is "clearly on the ground" only if BOTH are below these.
    ground_speed_kmh: float = 30.0
    ground_height_m: float = 40.0

    # A takeoff only counts once the aircraft got this high above ground (or
    # climbed this much above where it started, if there's no terrain data).
    # Filters out cars towing gliders and trailers on the road.
    takeoff_confirm_height_m: float = 50.0

    # A landing needs "clearly on the ground" beacons spanning at least this
    # long (and at least `ground_confirm_beacons` of them) ...
    ground_confirm_s: float = 20.0
    ground_confirm_beacons: int = 2
    # ... or this long if there's no airfield nearby (possible outlanding).
    # Much stricter, because a false landing away from an airfield most likely
    # means a glider flying slowly close to a slope in strong wind.
    outlanding_confirm_s: float = 60.0

    # Airborne aircraft that fall silent while low (below `lost_low_height_m`)
    # are considered landed after this long. Typical cause: FLARM switched off
    # right after landing, or no receiver coverage on the ground.
    lost_low_after_s: float = 10 * 60
    lost_low_height_m: float = 300.0
    # Airborne aircraft that fall silent while high are only given up after
    # this long (long cross-country flights out of coverage in the Alps).
    lost_high_after_s: float = 5 * 3600
    # Aircraft on the ground are forgotten after this much silence (memory).
    # Kept long so that an aircraft parked in the morning and next seen already
    # in the air still gets its takeoff airfield.
    forget_ground_after_s: float = 12 * 3600

    # If an aircraft reappears on the ground after being silent in the air for
    # longer than this, we did not see it land: the landing is estimated.
    landing_gap_s: float = 5 * 60

    # Beacons whose own timestamp is further than this from the time we
    # received them are dropped as stale/corrupt.
    max_beacon_age_s: float = 5 * 60
    max_beacon_future_s: float = 60

    # If no beacons at all were processed for this long, our feed was down;
    # silence during that time is not held against any aircraft.
    feed_gap_s: float = 120


# --------------------------------------------------------------------------- #
# Inputs and outputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Beacon:
    """One OGN position report, reduced to what detection needs.

    `address` is the 6-hex-digit device address and identifies the aircraft,
    independent of whether it was received as FLARM (FLR...), ICAO/ADS-B
    (ICA...) or OGN tracker (OGN...).
    """

    address: str
    timestamp: datetime  # when the aircraft sent it
    received_at: datetime  # when the APRS server relayed it - our stream clock
    latitude: float
    longitude: float
    altitude_m: float  # GPS altitude above mean sea level
    ground_speed_kmh: float
    climb_rate_ms: Optional[float] = None
    name: str = ""  # APRS name, e.g. "ICA4B4DF0" - informational only
    aircraft_type: int = 0


@dataclass
class FlightRecord:
    """One flight as the detector sees it. Sinks (CSV, database) receive it on
    takeoff and again on landing; `record_id` ties the two together."""

    address: str
    registration: str
    model: str
    takeoff_time: datetime
    takeoff_airport: Optional[Airport]
    # True if we did not see the takeoff itself (aircraft first seen in the
    # air, e.g. took off out of coverage) - takeoff_time is then "first seen".
    takeoff_estimated: bool = False
    takeoff_latitude: Optional[float] = None
    takeoff_longitude: Optional[float] = None

    landing_time: Optional[datetime] = None
    landing_airport: Optional[Airport] = None
    landing_estimated: bool = False
    landing_latitude: Optional[float] = None
    landing_longitude: Optional[float] = None

    max_height_m: float = 0.0
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def duration(self) -> Optional[timedelta]:
        if self.landing_time is None:
            return None
        return self.landing_time - self.takeoff_time


class EventKind(enum.Enum):
    TAKEOFF = "takeoff"
    LANDING = "landing"


@dataclass(frozen=True)
class FlightEvent:
    kind: EventKind
    flight: FlightRecord


# --------------------------------------------------------------------------- #
# Per-aircraft state
# --------------------------------------------------------------------------- #


class _Class(enum.Enum):
    """Classification of a single beacon (with hysteresis gap = UNSURE)."""

    AIR = "air"
    GROUND = "ground"
    UNSURE = "unsure"


class _State(enum.Enum):
    GROUND = "ground"
    AIRBORNE = "airborne"


@dataclass
class _Fix:
    """A remembered position."""

    time: datetime
    latitude: float
    longitude: float
    altitude_m: float
    height_m: Optional[float]


@dataclass
class _Track:
    address: str
    registration: str
    model: str
    state: _State
    last_time: Optional[datetime] = None  # timestamp of last accepted beacon
    last_fix: Optional[_Fix] = None
    last_air_fix: Optional[_Fix] = None  # last "clearly flying" beacon
    # Recent ground altitudes - fallback reference when there's no terrain data.
    ground_altitudes: deque = field(default_factory=lambda: deque(maxlen=5))

    # While on the GROUND: a possible takeoff in progress.
    takeoff_start: Optional[_Fix] = None
    # Consecutive beacons at/above takeoff_confirm_height_m in that takeoff.
    takeoff_high_count: int = 0
    # The takeoff started out of our sight (first beacon already high up);
    # `unseen_from` is where we last saw the aircraft standing, if anywhere.
    takeoff_unseen: bool = False
    unseen_from: Optional[_Fix] = None

    # While AIRBORNE: the current flight and a possible landing in progress.
    flight: Optional[FlightRecord] = None
    touchdown: Optional[_Fix] = None  # first low, non-flying beacon of this run
    ground_run_start: Optional[_Fix] = None  # first "clearly on ground" beacon
    ground_run_count: int = 0
    air_streak: int = 0  # consecutive "clearly flying" beacons

    # Set when the flight was restored from the database after a restart:
    # we have no beacons for it yet, so silence is counted from this time.
    restored_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# The detector
# --------------------------------------------------------------------------- #

TerrainLookup = Callable[[float, float], Optional[float]]
AirportLookup = Callable[[float, float], Optional[Airport]]
AircraftLookup = Callable[[str], tuple[str, str]]  # address -> (registration, model)


class FlightDetector:
    def __init__(
        self,
        terrain: TerrainLookup,
        nearest_airport: AirportLookup,
        aircraft_info: AircraftLookup = lambda address: ("", ""),
        rules: Optional[DetectionRules] = None,
    ):
        self._terrain = terrain
        self._nearest_airport = nearest_airport
        self._aircraft_info = aircraft_info
        self.rules = rules or DetectionRules()
        self._tracks: dict[str, _Track] = {}
        # Stream clock bookkeeping for sweep().
        self._last_sweep: Optional[datetime] = None
        self._feed_resumed_at: Optional[datetime] = None

    # ----------------------------------------------------------------- public

    def process(self, beacon: Beacon) -> list[FlightEvent]:
        """Feed one beacon; returns the takeoffs/landings it caused (0, 1 or 2)."""
        if not self._is_plausible(beacon):
            return []

        track = self._tracks.get(beacon.address)
        if track is None:
            return self._start_track(beacon)

        if track.last_time is not None and beacon.timestamp <= track.last_time:
            return []  # duplicate or out-of-order: never go back in time

        events: list[FlightEvent] = []
        # Reappearing after a long silence while airborne: decide before
        # treating this beacon as part of the same flight.
        if track.state is _State.AIRBORNE:
            events += self._handle_airborne(track, beacon)
        else:
            events += self._handle_ground(track, beacon)
        return events

    def sweep(self, now: datetime) -> list[FlightEvent]:
        """Close flights of aircraft that went silent, forget idle aircraft.

        `now` is the stream clock (receive time of the latest beacon), not the
        wall clock - if our feed is down, time effectively stands still.
        Call this regularly (e.g. every 30 s of stream time).
        """
        r = self.rules
        if self._last_sweep is not None and (now - self._last_sweep).total_seconds() > r.feed_gap_s:
            # We received nothing for a while: our connection was down, not
            # everyone's FLARM. Restart all silence timers from now.
            self._feed_resumed_at = now
        self._last_sweep = now

        events: list[FlightEvent] = []
        for address, track in list(self._tracks.items()):
            silent_since = track.last_time or track.restored_at or now
            if self._feed_resumed_at is not None and self._feed_resumed_at > silent_since:
                silent_since = self._feed_resumed_at
            silence = (now - silent_since).total_seconds()

            if track.state is _State.GROUND:
                if silence >= r.forget_ground_after_s:
                    del self._tracks[address]
                continue

            last = track.last_fix
            # Unknown position (restored after a restart): assume the worst
            # case, a long flight out of coverage.
            low = last is not None and (last.height_m is None or last.height_m < r.lost_low_height_m)
            limit = r.lost_low_after_s if low else r.lost_high_after_s
            if silence >= limit:
                events.append(self._land_after_signal_loss(track))
                del self._tracks[address]
        return events

    def restore(self, flight: FlightRecord, now: datetime) -> None:
        """Re-attach an open flight (from the database) after a restart, so its
        landing updates the same record instead of being lost."""
        if flight.address in self._tracks:
            return
        track = _Track(flight.address, flight.registration, flight.model, _State.AIRBORNE)
        track.flight = flight
        track.restored_at = now
        self._tracks[flight.address] = track

    def active_flights(self) -> list[FlightRecord]:
        return [t.flight for t in self._tracks.values() if t.state is _State.AIRBORNE and t.flight]

    def open_flight(self, address: str) -> Optional[FlightRecord]:
        """The flight `address` is on right now, if it's in the air."""
        track = self._tracks.get(address)
        return track.flight if track is not None and track.state is _State.AIRBORNE else None

    # ------------------------------------------------------ classification

    def _is_plausible(self, b: Beacon) -> bool:
        r = self.rules
        if not (-90 <= b.latitude <= 90 and -180 <= b.longitude <= 180):
            return False
        if b.ground_speed_kmh is None or b.ground_speed_kmh < 0 or b.altitude_m is None:
            return False
        age = (b.received_at - b.timestamp).total_seconds()
        return -r.max_beacon_future_s <= age <= r.max_beacon_age_s

    def _height(self, b: Beacon, track: Optional[_Track]) -> Optional[float]:
        """Height above ground: from terrain data, or else relative to the
        altitude we last saw this aircraft standing on the ground."""
        terrain = self._terrain(b.latitude, b.longitude)
        if terrain is not None:
            return b.altitude_m - terrain
        if track is not None and track.ground_altitudes:
            reference = sorted(track.ground_altitudes)[len(track.ground_altitudes) // 2]
            return b.altitude_m - reference
        return None

    def _classify(self, b: Beacon, height: Optional[float]) -> _Class:
        r = self.rules
        if b.ground_speed_kmh >= r.air_speed_kmh:
            return _Class.AIR
        if height is not None and height >= r.air_height_m:
            return _Class.AIR
        if b.ground_speed_kmh < r.ground_speed_kmh:
            if height is not None and height < r.ground_height_m:
                return _Class.GROUND
            if height is None and abs(b.climb_rate_ms or 0.0) < 1.0:
                # No terrain data and no ground reference: slow and level is the
                # best evidence we have.
                return _Class.GROUND
        return _Class.UNSURE

    # ------------------------------------------------------------ states

    def _start_track(self, b: Beacon) -> list[FlightEvent]:
        # A new aircraft always starts "on the ground". If it's actually
        # already flying, the normal takeoff confirmation (two beacons high
        # up) turns this into a flight with an estimated takeoff.
        registration, model = self._aircraft_info(b.address)
        track = _Track(b.address, registration, model, _State.GROUND)
        self._tracks[b.address] = track
        return self._handle_ground(track, b)

    def _handle_ground(self, track: _Track, b: Beacon) -> list[FlightEvent]:
        r = self.rules
        height = self._height(b, track)
        cls = self._classify(b, height)
        fix = _fix(b, height)
        high = height is not None and height >= r.takeoff_confirm_height_m

        gap = (b.timestamp - track.last_time).total_seconds() if track.last_time else None
        if gap is not None and gap > r.landing_gap_s:
            # Silent for a while: whatever takeoff roll we were watching is stale.
            self._reset_takeoff(track)

        if cls is _Class.AIR and track.takeoff_start is None:
            track.takeoff_start = fix
            track.ground_run_start, track.ground_run_count = None, 0
            if high:
                recently_on_ground = gap is not None and gap <= r.landing_gap_s
                if recently_on_ground and track.last_fix is not None:
                    # Standing there a moment ago, already climbing now: the
                    # takeoff roll fell between two beacons. It started right
                    # after the last one we saw on the ground.
                    track.takeoff_start = track.last_fix
                else:
                    # First beacon ever, or first after a long silence, and
                    # already up: we missed the takeoff.
                    track.takeoff_unseen = True
                    track.unseen_from = track.last_fix if gap is not None else None

        if track.takeoff_start is not None:
            start = track.takeoff_start
            if height is None:
                # No terrain: compare with where the takeoff roll started.
                high = b.altitude_m - start.altitude_m >= r.takeoff_confirm_height_m
            track.takeoff_high_count = track.takeoff_high_count + 1 if high else 0
            if track.takeoff_high_count >= 2:
                self._accept(track, b, fix, cls)
                return [self._take_off(track, fix)]

            if cls is _Class.GROUND:
                self._count_ground(track, fix)
                if self._ground_confirmed(track, fix, r.ground_confirm_s):
                    # Stopped again without ever climbing: ground movement.
                    self._reset_takeoff(track)
            elif cls is _Class.AIR:
                track.ground_run_start, track.ground_run_count = None, 0

        self._accept(track, b, fix, cls)
        return []

    def _handle_airborne(self, track: _Track, b: Beacon) -> list[FlightEvent]:
        r = self.rules
        height = self._height(b, track)
        cls = self._classify(b, height)
        fix = _fix(b, height)
        flight = track.flight
        assert flight is not None

        # Restored after a restart and first heard of again on the ground: it
        # landed while we weren't running.
        if cls is _Class.GROUND and track.last_air_fix is None and track.restored_at is not None:
            event = self._land(track, when=track.restored_at, where=fix, estimated=True)
            self._accept(track, b, fix, cls)
            return [event]

        # Silent in the air for a long time, now back on the ground: we did not
        # see the landing itself.
        last_air = track.last_air_fix
        if cls is _Class.GROUND and last_air is not None and track.touchdown is None:
            gap = (b.timestamp - last_air.time).total_seconds()
            if gap > r.landing_gap_s:
                event = self._land(track, when=last_air.time, where=fix if gap <= 1800 else last_air, estimated=True)
                self._accept(track, b, fix, cls)
                return [event]

        if height is not None:
            flight.max_height_m = max(flight.max_height_m, height)

        if cls is _Class.AIR:
            track.last_air_fix = fix
            track.air_streak += 1
            # Flying again (go-around, touch-and-go, or it was never landing):
            # forget the touchdown. But a single fast beacon right on the
            # ground is a GPS speed glitch during the rollout, not a go-around.
            off_the_ground = height is None or height >= r.ground_height_m
            if track.air_streak >= 2 or off_the_ground:
                track.touchdown = None
                track.ground_run_start, track.ground_run_count = None, 0
            self._accept(track, b, fix, cls)
            return []

        track.air_streak = 0

        # Not clearly flying: possibly landing. Remember the first low beacon
        # as the touchdown time (the rollout itself takes a while).
        low = height is None or height < r.ground_height_m
        if track.touchdown is None and (low or cls is _Class.GROUND):
            track.touchdown = fix

        if cls is _Class.GROUND:
            self._count_ground(track, fix)
            airport = self._nearest_airport(b.latitude, b.longitude)
            needed = r.ground_confirm_s if airport is not None else r.outlanding_confirm_s
            if self._ground_confirmed(track, fix, needed):
                touchdown = track.touchdown or track.ground_run_start or fix
                event = self._land(track, when=touchdown.time, where=fix, estimated=False)
                self._accept(track, b, fix, cls)
                return [event]

        self._accept(track, b, fix, cls)
        return []

    # ------------------------------------------------------------ helpers

    def _accept(self, track: _Track, b: Beacon, fix: _Fix, cls: _Class) -> None:
        track.last_time = b.timestamp
        track.last_fix = fix
        if cls is _Class.GROUND:
            track.ground_altitudes.append(b.altitude_m)

    def _count_ground(self, track: _Track, fix: _Fix) -> None:
        if track.ground_run_start is None:
            track.ground_run_start, track.ground_run_count = fix, 0
        track.ground_run_count += 1

    def _ground_confirmed(self, track: _Track, fix: _Fix, needed_s: float) -> bool:
        start = track.ground_run_start
        if start is None:
            return False
        return (
            track.ground_run_count >= self.rules.ground_confirm_beacons
            and (fix.time - start.time).total_seconds() >= needed_s
        )

    def _reset_takeoff(self, track: _Track) -> None:
        track.takeoff_start = None
        track.takeoff_high_count = 0
        track.takeoff_unseen = False
        track.unseen_from = None
        track.ground_run_start, track.ground_run_count = None, 0

    def _take_off(self, track: _Track, fix: _Fix) -> FlightEvent:
        start = track.takeoff_start
        assert start is not None
        if track.takeoff_unseen:
            origin = track.unseen_from
            airport = self._nearest_airport(origin.latitude, origin.longitude) if origin else None
        else:
            origin = start
            airport = self._nearest_airport(start.latitude, start.longitude)
        track.flight = FlightRecord(
            address=track.address,
            registration=track.registration,
            model=track.model,
            takeoff_time=start.time,
            takeoff_airport=airport,
            takeoff_estimated=track.takeoff_unseen,
            takeoff_latitude=origin.latitude if origin else None,
            takeoff_longitude=origin.longitude if origin else None,
            max_height_m=fix.height_m or 0.0,
        )
        track.state = _State.AIRBORNE
        self._reset_takeoff(track)
        track.last_air_fix = fix
        track.touchdown = None
        return FlightEvent(EventKind.TAKEOFF, track.flight)

    def _land(self, track: _Track, when: datetime, where: _Fix, estimated: bool) -> FlightEvent:
        flight = track.flight
        assert flight is not None
        flight.landing_time = max(when, flight.takeoff_time)
        flight.landing_airport = self._nearest_airport(where.latitude, where.longitude)
        flight.landing_latitude = where.latitude
        flight.landing_longitude = where.longitude
        flight.landing_estimated = estimated
        track.state = _State.GROUND
        track.flight = None
        track.touchdown = None
        self._reset_takeoff(track)
        return FlightEvent(EventKind.LANDING, flight)

    def _land_after_signal_loss(self, track: _Track) -> FlightEvent:
        # If we saw it touch down but not long enough to confirm, that's still
        # the best landing time we have; otherwise the last time we saw it.
        flight = track.flight
        assert flight is not None
        if track.ground_run_start is not None and track.touchdown is not None:
            return self._land(track, when=track.touchdown.time, where=track.last_fix or track.touchdown, estimated=False)
        where = track.last_fix
        when = where.time if where else flight.takeoff_time
        if where is None:
            # Restored after a restart and never heard from again.
            flight.landing_time = max(when, flight.takeoff_time)
            flight.landing_estimated = True
            track.state = _State.GROUND
            track.flight = None
            return FlightEvent(EventKind.LANDING, flight)
        return self._land(track, when=when, where=where, estimated=True)


def _fix(b: Beacon, height: Optional[float]) -> _Fix:
    return _Fix(b.timestamp, b.latitude, b.longitude, b.altitude_m, height)
