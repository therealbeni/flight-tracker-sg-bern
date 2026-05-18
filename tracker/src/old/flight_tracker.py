import math
from datetime import datetime, timezone
from typing import Optional

from old.models import Airport, FlightEvent, AIRCRAFT_TYPE_NAMES
from old.ddb import DeviceDatabase
from old.state_tracker import AircraftStateTracker


def _radius_from_airport(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class FlightTracker:
    """Detects takeoffs and landings near an airport."""

    def __init__(
        self,
        airport: Airport,
        ddb: DeviceDatabase,
        detection_radius_km: float = 5.0,
    ):
        self._airport = airport
        self._ddb = ddb
        self._detection_radius_km = detection_radius_km
        self._state_tracker = AircraftStateTracker(airport=airport)
        self._last_known_info: dict[str, dict] = {}

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
        timestamp = beacon.get("timestamp") or datetime.now(timezone.utc)
        ground_speed = beacon.get("ground_speed")
        altitude = beacon.get("altitude")

        # Store info in case this aircraft suffers a signal dropout
        address = beacon.get("address", "")
        ddb_entry = self._ddb.lookup(address)
        aircraft_type = (
            ddb_entry.model if ddb_entry and ddb_entry.model 
            else AIRCRAFT_TYPE_NAMES.get(beacon.get("aircraft_type"), "Unknown")
        )
        
        self._last_known_info[aircraft_id] = {
            "callsign": ddb_entry.registration if ddb_entry else "",
            "address": address,
            "aircraft_type": aircraft_type,
            "latitude": round(latitude, 5),
            "longitude": round(longitude, 5),
            "altitude_m": round(altitude, 1) if altitude is not None else None,
            "receiver": beacon.get("receiver_name", ""),
        }

        event, dept_time, duration = self._state_tracker.update(
            aircraft_id, altitude, ground_speed, timestamp
        )
        
        if event is None:
            return None

        return FlightEvent(
            timestamp=timestamp,
            event=event,
            aircraft_id=aircraft_id,
            callsign=self._last_known_info[aircraft_id]["callsign"],
            address=self._last_known_info[aircraft_id]["address"],
            aircraft_type=self._last_known_info[aircraft_id]["aircraft_type"],
            latitude=self._last_known_info[aircraft_id]["latitude"],
            longitude=self._last_known_info[aircraft_id]["longitude"],
            altitude_m=self._last_known_info[aircraft_id]["altitude_m"],
            ground_speed_kmh=round(ground_speed, 1) if ground_speed is not None else None,
            climb_rate_ms=round(beacon.get("climb_rate", 0), 2),
            receiver=self._last_known_info[aircraft_id]["receiver"],
            departure_time=dept_time,
            flight_duration_min=duration,
        )

    def check_timeouts(self, current_time: datetime) -> list[FlightEvent]:
        """Trigger landings for aircraft that dropped off radar."""
        events = []
        timeouts = self._state_tracker.check_timeouts(current_time)
        
        for aid, landing_time, dept_time, duration in timeouts:
            info = self._last_known_info.get(aid, {})
            events.append(FlightEvent(
                timestamp=landing_time,
                event="LANDING",
                aircraft_id=aid,
                callsign=info.get("callsign", ""),
                address=info.get("address", ""),
                aircraft_type=info.get("aircraft_type", "Unknown"),
                latitude=info.get("latitude", 0.0),
                longitude=info.get("longitude", 0.0),
                altitude_m=info.get("altitude_m", None),
                ground_speed_kmh=0.0, 
                climb_rate_ms=0.0,
                receiver=info.get("receiver", "TIMEOUT"),
                departure_time=dept_time,
                flight_duration_min=duration,
            ))
        return events