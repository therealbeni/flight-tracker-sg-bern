# How it works

The tracker connects to the OGN APRS network with an anonymous `N0CALL` login, receiving beacons from a broad region covering Switzerland. Each incoming position beacon passes through this pipeline:

1. **Phase classification** — ground speed and altitude above ground level (AGL, computed from SRTM elevation data) are compared against `FlightPhaseRules` thresholds. An aircraft is considered airborne if its speed exceeds `takeoff_speed_min` or its AGL exceeds `takeoff_agl_min`; otherwise it is on the ground.
2. **State transition detection** — `GlobalFlightTracker` tracks every aircraft in memory as a `FlightRecord`. When the classified state differs from the previous beacon, a takeoff or landing event is triggered.
3. **Nearest airport lookup** — on a state transition the aircraft's position is compared against all airports in `airports.csv`. The closest airport within `detection_radius_km` is attached to the event; if none is found, the airport field is left empty.
4. **DDB lookup** — the aircraft's FLARM address is looked up in the [OGN device database](https://ddb.glidernet.org/) (fetched at startup) to resolve registration and model name.
5. **Duration tracking** — takeoff time is stored on the `FlightRecord`. When a landing is detected, flight duration is computed from the takeoff timestamp.
6. **Per-airport CSV logger** — each `AirportLogger` watches for flights relevant to its airport (takeoff or landing ICAO matches). Flights are written as a single row on takeoff and the row is overwritten on landing so departure and arrival data share one record. Files rotate at midnight.
