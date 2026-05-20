# Configuration

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CSV_PATH` | `.` | Directory where CSV output files are written. If set to a file path, the directory containing that path is used. |

## Detection parameters

These are constructor arguments on `GlobalFlightTracker` and `FlightPhaseRules`, configured in `tracker/run.py`.

| Parameter | Default | Description |
|---|---|---|
| `detection_radius_km` | `5.0` | Radius around each airport within which a state transition is attributed to that airport (km). |
| `takeoff_speed_min` | `60.0` | Ground speed (km/h) above which an aircraft can be classified as airborne. |
| `takeoff_agl_min` | `10.0` | Altitude above ground level (m) above which an aircraft is classified as airborne regardless of speed. |

AGL is computed from SRTM elevation data at the aircraft's current position, not from a fixed airport elevation.

## Airports

Tracked airports are listed in `tracker/run.py`:

```python
AIRPORTS = [
    Airport(icao="LSZB", name="Bern Belp", lat=46.9144, lon=7.4990, elevation_m=510.0),
    Airport(icao="LSTZ", name="Zweisimmen", lat=46.551713, lon=7.381012, elevation_m=935.0),
]
```

`GlobalFlightTracker` also loads all airports from `tracker/src/airports.csv` (a standard OurAirports export) to identify airports for state transitions across the broader region. To add a tracked airport, add it to the `AIRPORTS` list and create an `AirportLogger` for it.
