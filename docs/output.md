# Output

## CSV files

Written to `DATA_DIR`, one file per day (UTC date of the takeoff):

- `{ICAO}_movements_{YYYY-MM-DD}.csv` - every flight taking off or landing at a home
  airfield (e.g. `LSZB_movements_2026-09-29.csv`), any aircraft.
- `sg-bern_movements_{YYYY-MM-DD}.csv` - every flight of a club glider, anywhere.

Each row is one flight. It is written on takeoff and completed on landing.

| Column | Description |
|---|---|
| `record_id` | UUID of the flight; the same as `flights.record_id` in the database |
| `plane_id` | OGN device address (6 hex digits, e.g. `4B4DF0`) |
| `callsign` | Registration from the OGN device database (e.g. `HB-2377`) |
| `plane_type` | Model from the OGN device database |
| `takeoff_airport` | ICAO code (or OurAirports ident) of the departure airfield; empty if none within 3 km |
| `landing_airport` | Same for the arrival airfield; empty = possible outlanding |
| `takeoff_time` | UTC ISO 8601 |
| `landing_time` | UTC ISO 8601; empty while in the air |
| `flight_duration_min` | Minutes, once landed |
| `takeoff_estimated` | `1` if the takeoff wasn't seen (first seen in the air) |
| `landing_estimated` | `1` if the landing wasn't seen (signal lost) |
| `max_height_m` | Highest height above ground seen |
| `landing_latitude`, `landing_longitude` | Where it landed (or was last seen) |

Files written by tracker versions before the new detector have the older column set (with `latitude`,
`longitude`, `altitude_m`, `speed_kmh` instead of the last five columns).

## Raw beacons

`raw/{YYYY-MM-DD}.aprs`: every APRS line received from a club glider, prefixed with the
receive time. Input for `replay.py`, see [How it works](how-it-works.md).
