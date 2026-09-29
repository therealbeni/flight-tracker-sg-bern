"""Glue between the OGN feed, the flight detector and the outputs ("sinks").

    OGN APRS line -> ogn-parser dict -> Beacon -> FlightDetector -> FlightEvent -> sinks

The detection logic itself lives in detection.py. This module only converts
data, keeps the sweep timer running and makes sure one failing sink (e.g. the
database being restarted) can never stop the others or the tracker.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, Union

from detection import Beacon, EventKind, FlightDetector, FlightEvent, FlightRecord


def beacon_from_ogn(parsed: dict, received_at: datetime) -> Optional[Beacon]:
    """Converts an ogn-parser result into a Beacon, or None if it isn't an
    aircraft position report with everything detection needs."""
    if parsed.get("aprs_type") != "position":
        return None
    address = parsed.get("address")
    timestamp = parsed.get("timestamp")
    values = [parsed.get(k) for k in ("latitude", "longitude", "altitude", "ground_speed")]
    if not address or timestamp is None or any(v is None for v in values):
        return None  # receiver/weather station beacons, incomplete packets
    latitude, longitude, altitude, ground_speed = values
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    # APRS beacons only carry a time of day. ogn-parser puts it on *today's*
    # date (it ignores reference_timestamp), which is wrong for a 23:59:59
    # beacon received at 00:00:02, and for every beacon when replaying an old
    # day. Move it to the day that puts it closest to when it was received.
    timestamp += timedelta(days=round((received_at - timestamp).total_seconds() / 86400))
    return Beacon(
        address=address.upper(),
        timestamp=timestamp,
        received_at=received_at,
        latitude=latitude,
        longitude=longitude,
        altitude_m=altitude,
        ground_speed_kmh=ground_speed,
        climb_rate_ms=parsed.get("climb_rate"),
        name=parsed.get("name", ""),
        aircraft_type=parsed.get("aircraft_type") or 0,
    )


class Sink(Protocol):
    def handle(self, event: FlightEvent) -> None: ...


class Tracker:
    """Feeds beacons to the detector and hands every takeoff/landing to all sinks."""

    def __init__(self, detector: FlightDetector, sinks: Iterable[Sink], sweep_every_s: float = 30.0,
                 announce: Callable[[FlightRecord], bool] = lambda flight: True):
        self.detector = detector
        self.sinks = list(sinks)
        # Which takeoffs/landings to print to the log (all aircraft in range
        # would be hundreds per hour).
        self._announce = announce
        self._sweep_every = timedelta(seconds=sweep_every_s)
        self._next_sweep: Optional[datetime] = None

    def process(self, beacon: Beacon) -> None:
        events = self.detector.process(beacon)
        # The sweep runs on the stream clock (receive time), see FlightDetector.sweep.
        now = beacon.received_at
        if self._next_sweep is None or now >= self._next_sweep:
            events += self.detector.sweep(now)
            self._next_sweep = now + self._sweep_every
        for event in events:
            self.dispatch(event)

    def dispatch(self, event: FlightEvent) -> None:
        f = event.flight
        if self._announce(f):
            print(
                f"{event.kind.value}: {f.registration or f.address} "
                f"{_icao(f.takeoff_airport) or '?'} {f.takeoff_time:%H:%M:%S}"
                + (f" -> {_icao(f.landing_airport) or '?'} {f.landing_time:%H:%M:%S}" if f.landing_time else "")
                + (" (estimated)" if f.takeoff_estimated or f.landing_estimated else ""),
                file=sys.stderr,
            )
        for sink in self.sinks:
            try:
                sink.handle(event)
            except Exception as exc:  # noqa: BLE001 - a broken sink must not stop tracking
                print(f"{type(sink).__name__} failed on {event.kind.value} of {f.registration}: {exc}", file=sys.stderr)


def _icao(airport) -> str:
    return airport.icao if airport is not None else ""


CSV_FIELDNAMES = [
    "record_id", "plane_id", "callsign", "plane_type",
    "takeoff_airport", "landing_airport",
    "takeoff_time", "landing_time", "flight_duration_min",
    "takeoff_estimated", "landing_estimated",
    "max_height_m", "landing_latitude", "landing_longitude",
]


class CsvLogger:
    """Writes one row per flight into a daily CSV file `{name}_movements_{date}.csv`.

    The row is written on takeoff and rewritten on landing (same record_id).
    The date is the UTC date of the takeoff, so a flight always stays in one file.
    `wants` decides which flights belong in this file (e.g. "touches LSZB",
    "is a club aircraft"). Flights shorter than `min_flight_duration_min` are
    removed again on landing - they are ground movements that slipped through.
    """

    def __init__(
        self,
        name: str,
        wants: Callable[[FlightRecord], bool],
        output_dir: Union[str, Path] = ".",
        min_flight_duration_min: float = 1.0,
    ):
        self.name = name
        self._wants = wants
        self._output_dir = Path(output_dir)
        self._min_duration = timedelta(minutes=min_flight_duration_min)

    @classmethod
    def for_airport(cls, icao: str, **kwargs) -> "CsvLogger":
        return cls(icao, lambda f: icao in (_icao(f.takeoff_airport), _icao(f.landing_airport)), **kwargs)

    @classmethod
    def for_fleet(cls, name: str, addresses: Callable[[], Iterable[str]], **kwargs) -> "CsvLogger":
        return cls(name, lambda f: f.address in addresses(), **kwargs)

    def path_for(self, flight: FlightRecord) -> Path:
        return self._output_dir / f"{self.name}_movements_{flight.takeoff_time.date()}.csv"

    def handle(self, event: FlightEvent) -> None:
        flight = event.flight
        if not self._wants(flight):
            return
        path = self.path_for(flight)
        rows = _read_rows(path)
        if event.kind is EventKind.LANDING and is_too_short(flight, self._min_duration):
            if rows.pop(flight.record_id, None) is None:
                return
        else:
            rows[flight.record_id] = _csv_row(flight)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows.values())


def is_too_short(flight: FlightRecord, min_duration: timedelta) -> bool:
    """A seen takeoff followed by a seen landing within `min_duration` was not a
    real flight. Estimated times are never "too short": a flight that lost
    signal right after takeoff is real, we just don't know how long it was."""
    return (
        flight.duration is not None
        and flight.duration < min_duration
        and not (flight.takeoff_estimated or flight.landing_estimated)
    )


def _read_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["record_id"]: row for row in csv.DictReader(f) if row.get("record_id")}


def _csv_row(f: FlightRecord) -> dict:
    duration = f.duration
    return {
        "record_id": f.record_id,
        "plane_id": f.address,
        "callsign": f.registration,
        "plane_type": f.model,
        "takeoff_airport": _icao(f.takeoff_airport),
        "landing_airport": _icao(f.landing_airport),
        "takeoff_time": f.takeoff_time.isoformat(),
        "landing_time": f.landing_time.isoformat() if f.landing_time else "",
        "flight_duration_min": round(duration.total_seconds() / 60, 1) if duration is not None else "",
        "takeoff_estimated": int(f.takeoff_estimated),
        "landing_estimated": int(f.landing_estimated),
        "max_height_m": round(f.max_height_m),
        "landing_latitude": f.landing_latitude if f.landing_latitude is not None else "",
        "landing_longitude": f.landing_longitude if f.landing_longitude is not None else "",
    }


class RawRecorder:
    """Keeps the raw APRS lines of selected aircraft, one file per UTC day.

    Detection problems can then be reproduced exactly by replaying a day
    (see tracker/replay.py) instead of guessing from the resulting CSV.
    """

    def __init__(self, output_dir: Union[str, Path], addresses: Callable[[], Iterable[str]]):
        self._dir = Path(output_dir)
        self._addresses = addresses

    def record(self, beacon: Beacon, raw_message: str) -> None:
        if beacon.address not in self._addresses():
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{beacon.received_at.date()}.aprs"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{beacon.received_at.isoformat()} {raw_message}\n")
