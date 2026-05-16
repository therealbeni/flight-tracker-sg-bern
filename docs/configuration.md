# Configuration

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CSV_PATH` | `lszb_movements.csv` | Base path for CSV output. A date suffix (`_YYYY-MM-DD`) is appended automatically. |

## Detection parameters

These are constructor arguments on `FlightTracker` and `AircraftStateTracker`, configured in `tracker/run.py`.

| Parameter | Default | Description |
|---|---|---|
| `detection_radius_km` | `5.0` | Radius around the airport within which beacons are processed (km). |
| `airborne_alt_m` | `20.0` | Altitude above airport elevation (m) above which an aircraft is considered airborne. |
| `ground_alt_m` | `5.0` | Altitude above airport elevation (m) below which an aircraft is considered on ground. |
| `confirm_beacons` | `3` | Consecutive beacons required to confirm a state transition. |

The gap between `ground_alt_m` and `airborne_alt_m` forms a hysteresis band: aircraft in that range hold their previous state.

## Airport

The airport is defined in `tracker/run.py`:

```python
LSZB = Airport(icao="LSZB", name="Bern Belp", lat=46.9144, lon=7.4990, elevation_m=510.0)
```

To track a different airport, replace the coordinates and elevation with the target airport's values.
