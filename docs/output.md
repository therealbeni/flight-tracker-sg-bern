# Output

Each `AirportLogger` writes a daily CSV file named `{ICAO}_movements_{YYYY-MM-DD}.csv` (e.g. `LSZB_movements_2025-06-01.csv`). A new file is created automatically at midnight local time.

Each row represents one flight. The row is written when a takeoff is detected and updated in place when the aircraft lands, so departure and arrival data share a single record.

| Column | Description |
|---|---|
| `record_id` | UUID uniquely identifying this flight leg |
| `plane_id` | APRS callsign / OGN device identifier (e.g. `ICA4B08C3`) |
| `callsign` | Registration from OGN DDB (e.g. `HB-DIH`) |
| `plane_type` | Model name from DDB, or beacon type (`Glider`, `Powered aircraft`, etc.) |
| `latitude` | Latitude at time of last update (decimal degrees) |
| `longitude` | Longitude at time of last update (decimal degrees) |
| `altitude_m` | Altitude in metres AMSL at time of last update |
| `speed_kmh` | Ground speed in km/h at time of last update |
| `takeoff_airport` | ICAO code of the departure airport, if detected within `detection_radius_km` |
| `landing_airport` | ICAO code of the arrival airport, if detected within `detection_radius_km` |
| `takeoff_time` | UTC ISO 8601 timestamp of the takeoff event |
| `landing_time` | UTC ISO 8601 timestamp of the landing event |
| `flight_duration_min` | Flight duration in minutes — populated once both takeoff and landing are recorded |
