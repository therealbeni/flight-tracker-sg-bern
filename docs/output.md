# Output

Events are appended to a daily CSV file named `<base>_YYYY-MM-DD.csv` (e.g. `lszb_movements_2025-06-01.csv`). A new file is created automatically at midnight UTC.

| Column | Description |
|---|---|
| `timestamp` | UTC ISO 8601 timestamp of the event |
| `event` | `TAKEOFF` or `LANDING` |
| `aircraft_id` | APRS callsign (e.g. `ICA4B08C3`) |
| `callsign` | Registration from OGN DDB (e.g. `HB-DIH`) |
| `address` | FLARM/ICAO hex address |
| `aircraft_type` | Model name from DDB, or beacon type (`Glider`, `Powered aircraft`, etc.) |
| `latitude` | Latitude at time of event (decimal degrees, 5 dp) |
| `longitude` | Longitude at time of event (decimal degrees, 5 dp) |
| `altitude_m` | Altitude in metres AMSL |
| `ground_speed_kmh` | Ground speed in km/h |
| `climb_rate_ms` | Climb rate in m/s |
| `receiver` | OGN receiver that picked up the beacon |
| `departure_time` | Takeoff timestamp — populated on landing if the aircraft took off in the same session |
| `flight_duration_min` | Flight duration in minutes — populated on landing if departure was recorded |
