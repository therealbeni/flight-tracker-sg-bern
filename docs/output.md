# Output

## Flights

Every takeoff and landing of a club aircraft (the `gliders` table) goes into the
web app's `flights` table: created on takeoff, completed on landing. See
`tracker/src/db_sink.py`.

## Raw beacons

`raw/{YYYY-MM-DD}.aprs`: every APRS line received from a club glider, prefixed with the
receive time. Input for `replay.py`, see [How it works](how-it-works.md).
