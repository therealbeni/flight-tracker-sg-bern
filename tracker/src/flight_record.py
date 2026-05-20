import uuid
from datetime import timedelta
from typing import Optional
from models import FlightState, Airport

class FlightRecord:
    def __init__(self, plane_id, plane_type, callsign, flight_state, timestamp):
        self.record_id = str(uuid.uuid4())  # Unique ID for the DB row
        self.plane_id = plane_id            # Physical plane ID from ogn
        self.plane_type = plane_type
        self.callsign = callsign

        self.latitude = None
        self.longitude = None
        self.height = None
        self.speed = None
        self.vertical_speed = None

        self.last_updated = timestamp
        self.flight_state = flight_state

        self._takeoff_time = None
        self._landing_time = None
        self._takeoff_airport = None
        self._landing_airport = None

    def update(self, latitude, longitude, height, speed, vertical_speed, timestamp):
        self.latitude = latitude
        self.longitude = longitude
        self.height = height
        self.speed = speed
        self.vertical_speed = vertical_speed
        self.last_updated = timestamp

    def takeoff(self, airport: Optional[Airport]):
        self._takeoff_airport = airport
        self._takeoff_time = self.last_updated
        self.flight_state = FlightState.FLYING

    def land(self, airport: Optional[Airport]):
        self._landing_airport = airport
        self._landing_time = self.last_updated
        self.flight_state = FlightState.GROUND

    @property
    def takeoff_airport(self):
        return self._takeoff_airport

    @property
    def landing_airport(self):
        return self._landing_airport

    @property
    def takeoff_time(self):
        return self._takeoff_time

    @property
    def landing_time(self):
        return self._landing_time
    
    @property
    def flight_duration(self) -> timedelta | None:
        # Completed flight
        if self._takeoff_time and self._landing_time:
            return self._landing_time - self._takeoff_time
        
        # In the air
        if self._takeoff_time and not self._landing_time:
            return self.last_updated - self._takeoff_time
        
        # Landing but no takeoff (data anomaly)
        if not self._takeoff_time and self._landing_time:
            return None
        
        # Currently on Ground prior to takeoff
        return None