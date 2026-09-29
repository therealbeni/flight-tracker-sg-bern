"""The glue in flight_tracker.py: OGN parsing, CSV output, sink isolation."""

import csv
from datetime import datetime, timedelta, timezone

from detection import EventKind
from flight_tracker import CsvLogger, RawRecorder, Tracker, beacon_from_ogn
from sim import LSPG, Sim, make_detector

UTC = timezone.utc


def ogn(**overrides) -> dict:
    """An ogn-parser result for a FLARM position beacon."""
    parsed = {
        "aprs_type": "position", "address": "4b4df0", "name": "FLR4B4DF0", "aircraft_type": 1,
        "timestamp": datetime(2026, 9, 29, 13, 40, 5, tzinfo=UTC),
        "latitude": 46.9093, "longitude": 7.4967, "altitude": 511.1, "ground_speed": 59.3, "climb_rate": 0.1,
    }
    parsed.update(overrides)
    return parsed


def test_position_beacon_is_converted():
    b = beacon_from_ogn(ogn(), received_at=datetime(2026, 9, 29, 13, 40, 7, tzinfo=UTC))
    assert b.address == "4B4DF0"
    assert b.timestamp == datetime(2026, 9, 29, 13, 40, 5, tzinfo=UTC)
    assert (b.altitude_m, b.ground_speed_kmh, b.climb_rate_ms) == (511.1, 59.3, 0.1)


def test_non_position_and_incomplete_beacons_are_skipped():
    received = datetime(2026, 9, 29, 13, 40, 7, tzinfo=UTC)
    assert beacon_from_ogn(ogn(aprs_type="status"), received) is None
    assert beacon_from_ogn(ogn(ground_speed=None), received) is None
    assert beacon_from_ogn(ogn(address=None), received) is None


def test_beacon_just_before_midnight_keeps_its_day():
    # ogn-parser dates every beacon "today": 23:59:59 received at 00:00:02
    # would otherwise be almost 24 h in the future.
    parsed = ogn(timestamp=datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC))
    b = beacon_from_ogn(parsed, received_at=datetime(2026, 9, 30, 0, 0, 2, tzinfo=UTC))
    assert b.timestamp == datetime(2026, 9, 29, 23, 59, 59, tzinfo=UTC)


def test_replayed_beacon_gets_the_day_it_was_received():
    parsed = ogn(timestamp=datetime(2026, 9, 29, 13, 40, 5, tzinfo=UTC))  # parsed "today"
    b = beacon_from_ogn(parsed, received_at=datetime(2026, 9, 12, 13, 40, 6, tzinfo=UTC))
    assert b.timestamp == datetime(2026, 9, 12, 13, 40, 5, tzinfo=UTC)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fly(sinks, sim):
    tracker = Tracker(make_detector(), sinks)
    for beacon in sim.beacons:
        tracker.process(beacon)


def test_csv_row_written_on_takeoff_and_completed_on_landing(tmp_path):
    logger = CsvLogger.for_airport("LSZB", output_dir=tmp_path)
    sim = Sim().park(120).accelerate(0, 70, 16).climb(to_height=600, speed=110)
    tracker = Tracker(make_detector(), [logger])
    for beacon in sim.beacons:
        tracker.process(beacon)
    [row] = read_csv(tmp_path / "LSZB_movements_2026-09-26.csv")
    assert row["callsign"] == "HB-1811" and row["takeoff_airport"] == "LSZB" and row["landing_time"] == ""

    already_sent = len(sim.beacons)
    sim.cruise(1200).fly_to(46.9144, 7.4990).descend(0).decelerate(90, 0, 24).park(120)
    for beacon in sim.beacons[already_sent:]:
        tracker.process(beacon)
    [row] = read_csv(tmp_path / "LSZB_movements_2026-09-26.csv")
    assert row["landing_airport"] == "LSZB" and float(row["flight_duration_min"]) > 20
    assert row["takeoff_estimated"] == "0" and row["landing_estimated"] == "0"


def test_airport_logger_ignores_flights_elsewhere(tmp_path):
    fly([CsvLogger.for_airport("LSTZ", output_dir=tmp_path)], Sim().park(120).local_flight(20).park(120))
    assert list(tmp_path.iterdir()) == []


def test_fleet_logger_logs_club_aircraft_anywhere(tmp_path):
    sim = Sim(lat=LSPG.lat, lon=LSPG.lon, ground_alt=470).park(120).local_flight(20).park(120)
    fly([CsvLogger.for_fleet("sg-bern", lambda: {"4B4BBA": "HB-1811"}, output_dir=tmp_path)], sim)
    [row] = read_csv(tmp_path / "sg-bern_movements_2026-09-26.csv")
    assert row["takeoff_airport"] == row["landing_airport"] == "LSPG"


def test_several_flights_append_to_the_same_day_file(tmp_path):
    sim = Sim().park(120).local_flight(15).park(600).local_flight(15).park(120)
    fly([CsvLogger.for_airport("LSZB", output_dir=tmp_path)], sim)
    assert len(read_csv(tmp_path / "LSZB_movements_2026-09-26.csv")) == 2


class Broken:
    def handle(self, event):
        raise RuntimeError("database is down")


class Collect:
    def __init__(self):
        self.events = []

    def handle(self, event):
        self.events.append(event)


def test_a_failing_sink_does_not_stop_the_others():
    collect = Collect()
    fly([Broken(), collect], Sim().park(120).local_flight(20).park(120))
    assert [e.kind for e in collect.events] == [EventKind.TAKEOFF, EventKind.LANDING]


def test_tracker_sweeps_on_the_stream_clock():
    # Signal lost low on final: only the periodic sweep can close the flight,
    # driven by other aircraft's beacons keeping the stream clock going.
    collect = Collect()
    tracker = Tracker(make_detector(), [collect])
    sim = Sim().park(120).accelerate(0, 70, 16).climb(to_height=400, speed=110).descend(to_height=150)
    other = Sim(address="AAAAAA", start=sim.t).park(20 * 60)
    for beacon in sim.beacons + other.beacons:
        tracker.process(beacon)
    kinds = [e.kind for e in collect.events]
    assert kinds == [EventKind.TAKEOFF, EventKind.LANDING]
    assert collect.events[1].flight.landing_estimated


def test_raw_recorder_keeps_only_selected_aircraft(tmp_path):
    recorder = RawRecorder(tmp_path, lambda: {"4B4BBA": "HB-1811"})
    club = Sim().park(8).beacons[0]
    other = Sim(address="AAAAAA").park(8).beacons[0]
    recorder.record(club, "FLR4B4BBA>OGFLR:...")
    recorder.record(other, "FLRAAAAAA>OGFLR:...")
    [line] = (tmp_path / "2026-09-26.aprs").read_text().splitlines()
    assert line.endswith(" FLR4B4BBA>OGFLR:...")
    assert datetime.fromisoformat(line.split(" ")[0]) == club.received_at
