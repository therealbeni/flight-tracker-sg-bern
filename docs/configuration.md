# Configuration

## Environment variables (tracker)

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `.` | Where raw beacon recordings (`raw/`) are written. |
| `SRTM_CACHE_DIR` | srtm.py default | Terrain tile cache. Keep it on a persistent volume. |
| `DATABASE_URL` | see `shared/database.py` | The web app's database. |

## Club gliders

The tracker takes the fleet from the `gliders` table (managed in the web app under
Admin), matched by OGN device address (`ogn_device_id`, the 6 hex digits of the FLARM
ID). Changes are picked up within 5 minutes, no restart needed.

Only these aircraft are tracked; beacons of all other aircraft are only used to
know that the OGN feed is up.

## Airfields

Takeoffs and landings are attributed to any airfield in `tracker/src/airports.csv`
within 3 km; an airfield a club glider lands at is added to the database
automatically.

## Detection thresholds

All thresholds are fields of `DetectionRules` in `tracker/src/detection.py`, each with a
comment explaining it; see [How it works](how-it-works.md) for the reasoning. After
changing one, run `dev/test.sh` and replay a few recorded days (`replay.py`).
