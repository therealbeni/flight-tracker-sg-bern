# Flight Tracker SG Bern

Logs takeoffs and landings at Bern Belp Airport (LSZB) by listening to live [OGN](https://www.glidernet.org/) APRS beacons. Each detected movement is written to a daily CSV file.

## How it works

The tracker connects to the OGN APRS network and receives position beacons from FLARM-equipped aircraft within a configurable radius of the airport. For each aircraft, it tracks altitude relative to the airport elevation and applies hysteresis with a confirmation count to filter out noise during ground rolls. When an aircraft transitions from ground to airborne (or vice versa), a `TAKEOFF` or `LANDING` event is emitted.

Aircraft registration and model are resolved from the [OGN device database](https://ddb.glidernet.org/). Flight duration is computed for aircraft that take off and land within the same session.

## Output

Events are appended to a daily CSV file (`lszb_movements_YYYY-MM-DD.csv`):

| Column | Description |
|---|---|
| `timestamp` | UTC ISO 8601 timestamp of the event |
| `event` | `TAKEOFF` or `LANDING` |
| `aircraft_id` | APRS callsign (e.g. `ICA4B08C3`) |
| `callsign` | Registration from OGN DDB (e.g. `HB-DIH`) |
| `address` | FLARM/ICAO hex address |
| `aircraft_type` | Model name from DDB, or type from beacon (Glider, Powered aircraft, etc.) |
| `latitude` / `longitude` | Position at time of event |
| `altitude_m` | Altitude in metres (AMSL) |
| `ground_speed_kmh` | Ground speed in km/h |
| `climb_rate_ms` | Climb rate in m/s |
| `receiver` | OGN receiver that picked up the beacon |
| `departure_time` | Takeoff timestamp (populated on landing) |
| `flight_duration_min` | Flight duration in minutes (populated on landing) |

## Running with Docker Compose

```bash
docker compose up -d
```

CSV files are written to `./data/` on the host. The container restarts automatically unless explicitly stopped.

## Running directly

```bash
pip install ogn-client ogn-parser
CSV_PATH=lszb_movements.csv python tracker/run.py
```

## Configuration

The following environment variables are supported:

| Variable | Default | Description |
|---|---|---|
| `CSV_PATH` | `lszb_movements.csv` | Base path for CSV output (date suffix is appended automatically) |

Detection parameters (radius, altitude thresholds, confirmation count) can be adjusted in [tracker/run.py](tracker/run.py) and [tracker/src/flight_tracker.py](tracker/src/flight_tracker.py).

## Project structure

```
tracker/
  run.py               # Entry point; configures airport and starts the APRS client
  src/
    flight_tracker.py  # Detects takeoffs and landings from position beacons
    state_tracker.py   # Per-aircraft ground/airborne state machine with hysteresis
    csv_logger.py      # Writes flight events to daily-rotating CSV files
    ddb.py             # Fetches and queries the OGN device database
    models.py          # Dataclasses: Airport, AircraftInfo, FlightEvent
examples/
  ogn_api_test.py      # Minimal OGN APRS client example
```

## Dependencies

- [ogn-client](https://github.com/glidernet/python-ogn-client) — APRS client for OGN
