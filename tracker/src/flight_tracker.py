import math
from datetime import datetime, timezone
from typing import Optional

from models import Airport, FlightEvent, AIRCRAFT_TYPE_NAMES
from ddb import DeviceDatabase
from state_tracker import AircraftStateTracker


def _radius_from_airport(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class FlightTracker:
    """Detects takeoffs and landings near an airport and computes flight duration.

    An aircraft that takes off from the airport is tracked in memory. When it
    lands at the same airport later in the same session, the flight duration is
    computed and included in the landing event.
    """

    def __init__(
        self,
        airport: Airport,
        ddb: DeviceDatabase,
        detection_radius_km: float = 5.0,
        airborne_alt_m: float = 20.0,
        ground_alt_m: float = 5.0,
        confirm_beacons: int = 3,
    ):
        self._airport = airport
        self._ddb = ddb
        self._detection_radius_km = detection_radius_km
        self._state_tracker = AircraftStateTracker(
            airport=airport,
            airborne_alt_m=airborne_alt_m,
            ground_alt_m=ground_alt_m,
            confirm_beacons=confirm_beacons,
        )
        self._departures: dict[str, datetime] = {}

    def process_beacon(self, beacon: dict) -> Optional[FlightEvent]:
        if beacon.get("aprs_type") != "position":
            return None

        latitude = beacon.get("latitude")
        longitude = beacon.get("longitude")
        if latitude is None or longitude is None:
            return None

        if _radius_from_airport(latitude, longitude, self._airport.lat, self._airport.lon) > self._detection_radius_km:
            return None

        aircraft_id = beacon.get("name", "")
        event = self._state_tracker.update(aircraft_id, beacon.get("altitude"))
        if event is None:
            return None

        timestamp = beacon.get("timestamp") or datetime.now(timezone.utc)
        address = beacon.get("address", "")
        ddb_entry = self._ddb.lookup(address)

        aircraft_type = (
            ddb_entry.model
            if ddb_entry and ddb_entry.model
            else AIRCRAFT_TYPE_NAMES.get(beacon.get("aircraft_type"), "Unknown")
        )

        departure_time = None
        flight_duration_min = None

        if event == "TAKEOFF":
            self._departures[aircraft_id] = timestamp
        elif event == "LANDING" and aircraft_id in self._departures:
            dep = self._departures.pop(aircraft_id)
            departure_time = dep
            flight_duration_min = round((timestamp - dep).total_seconds() / 60, 1)

        ground_speed = beacon.get("ground_speed")
        altitude = beacon.get("altitude")

        return FlightEvent(
            timestamp=timestamp,
            event=event,
            aircraft_id=aircraft_id,
            callsign=ddb_entry.registration if ddb_entry else "",
            address=address,
            aircraft_type=aircraft_type,
            latitude=round(latitude, 5),
            longitude=round(longitude, 5),
            altitude_m=round(altitude, 1) if altitude is not None else None,
            ground_speed_kmh=round(ground_speed, 1) if ground_speed is not None else None,
            climb_rate_ms=round(beacon.get("climb_rate", 0), 2),
            receiver=beacon.get("receiver_name", ""),
            departure_time=departure_time,
            flight_duration_min=flight_duration_min,
        )
