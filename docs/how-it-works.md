# How it works

The tracker connects to the OGN APRS network with an anonymous `N0CALL` login, filtered to a 15 km radius around LSZB. Each incoming position beacon passes through this pipeline:

1. **Radius filter** — beacons from aircraft outside `detection_radius_km` are discarded.
2. **State machine** — each aircraft maintains a ground/airborne state. Altitude relative to airport elevation is compared against two thresholds. A transition is only confirmed after `confirm_beacons` consecutive beacons agree on the new state, which filters out noise during ground rolls and short dips.
3. **Hysteresis band** — altitudes between `ground_alt_m` and `airborne_alt_m` leave the previous state unchanged, preventing rapid toggling near the threshold.
4. **DDB lookup** — on a confirmed event the aircraft's FLARM address is looked up in the [OGN device database](https://ddb.glidernet.org/) to resolve registration and model name.
5. **Duration tracking** — takeoff timestamps are held in memory. When a landing is detected for an aircraft that took off in the same session, flight duration is computed and included in the landing event.
6. **CSV logger** — the event is appended to the current day's CSV file, which rotates at midnight.
