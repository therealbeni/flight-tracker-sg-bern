import uuid
from datetime import timedelta
from typing import Optional
from models import FlightState, Airport

class FlightRecord:
    def __init__(self, plane_id, plane_type, callsign, flight_state, time):
        self.record_id = str(uuid.uuid4())  # Unique ID for the DB row
        self.plane_id = plane_id            # Physical plane ID from ogn
        self.plane_type = plane_type
        self.callsign = callsign

        self.latitude = None
        self.longitude = None
        self.height = None
        self.speed = None
        self.vertical_speed = None

        self.last_updated = time
        self.flight_state: FlightState = flight_state

        self._takeoff_time = None
        self._landing_time = None
        self._takeoff_airport = None
        self._landing_airport = None
        
        # State flag for database locking
        self.is_locked = False

    def update(self, latitude, longitude, height, speed, vertical_speed, time):
        if self.is_locked:
            raise ValueError(f"Flight {self.record_id} is locked. Cannot update a landed flight.")

        self.latitude = latitude
        self.longitude = longitude
        self.height = height
        self.speed = speed
        self.vertical_speed = vertical_speed
        self.last_updated = time

    def takeoff(self, airport:Airport):
        if self.is_locked:
            raise ValueError("Cannot takeoff a locked flight record. Create a new instance.")
            
        self._takeoff_airport = airport
        self._takeoff_time = self.last_updated
        self.flight_state = FlightState.FLYING

    def land(self, airport: Optional[Airport]):
        if self.is_locked:
            return  # Already landed and locked

        self._landing_airport = airport
        self._landing_time = self.last_updated
        self.flight_state = FlightState.GROUND
        
        # Lock the record so it can no longer be updated
        self.is_locked = True

    @property
    def flight_state_current(self):
        return self.flight_state

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
        
        # Landing but no takeoff (Data anomaly)
        if not self._takeoff_time and self._landing_time:
            raise ValueError("Invalid Flight data: Landing time exists but no Takeoff")
        
        # Currently on Ground prior to takeoff
        return None