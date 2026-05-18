from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from old.models import Airport

@dataclass
class _AircraftState:
    confirmed_on_ground: bool
    pending_on_ground: bool
    pending_count: int
    departure_time: Optional[datetime] = None
    last_seen: Optional[datetime] = None

class AircraftStateTracker:
    """Tracks whether each aircraft is on ground or airborne near a given airport.

    Uses ground speed and altitude relative to airport elevation, combined
    with a confirmation count and timeout buffer to prevent false transitions.
    """

    def __init__(
        self,
        airport: Airport,
        takeoff_speed_kmh: float = 40.0,
        landing_speed_kmh: float = 15.0,
        airborne_alt_m: float = 30.0,
        confirm_beacons: int = 2,
        timeout_min: float = 3.0,
    ):
        self._airport = airport
        self._takeoff_speed = takeoff_speed_kmh
        self._landing_speed = landing_speed_kmh
        self._airborne_alt = airborne_alt_m
        self._confirm_beacons = confirm_beacons
        self._timeout_delta = timedelta(minutes=timeout_min)
        self._states: dict[str, _AircraftState] = {}

    def _classify(self, altitude: float, speed: float, prev_on_ground: Optional[bool]) -> Optional[bool]:
        alt_above_airport = altitude - self._airport.elevation_m
        
        # If moving fast, it is taking off or flying
        if speed >= self._takeoff_speed:
            return False
            
        # If slow AND near the ground, it has landed
        if speed <= self._landing_speed and alt_above_airport <= self._airborne_alt:
            return True
            
        # Hysteresis: keep previous state if in between thresholds
        return prev_on_ground

    def update(
        self, aircraft_id: str, altitude: Optional[float], speed: Optional[float], timestamp: datetime
    ) -> Tuple[Optional[str], Optional[datetime], Optional[float]]:
        """Process new telemetry. Returns (Event_Type, Departure_Time, Duration_Min)."""
        if altitude is None or speed is None:
            return None, None, None

        state = self._states.get(aircraft_id)

        if state is None:
            classification = self._classify(altitude, speed, None)
            is_ground = classification if classification is not None else True
            self._states[aircraft_id] = _AircraftState(
                confirmed_on_ground=is_ground,
                pending_on_ground=is_ground,
                pending_count=1,
                last_seen=timestamp
            )
            return None, None, None

        state.last_seen = timestamp
        current = self._classify(altitude, speed, state.confirmed_on_ground)
        
        if current is None:
            return None, None, None

        if current == state.pending_on_ground:
            state.pending_count += 1
        else:
            state.pending_on_ground = current
            state.pending_count = 1

        if state.pending_count < self._confirm_beacons:
            return None, None, None
            
        if state.pending_on_ground == state.confirmed_on_ground:
            return None, None, None

        # State transition confirmed
        state.confirmed_on_ground = state.pending_on_ground
        event = "LANDING" if state.pending_on_ground else "TAKEOFF"
        
        dept_time = None
        duration = None
        
        if event == "TAKEOFF":
            state.departure_time = timestamp
        elif event == "LANDING":
            dept_time = state.departure_time
            if dept_time:
                duration = round((timestamp - dept_time).total_seconds() / 60.0, 1)
            state.departure_time = None

        return event, dept_time, duration

    def check_timeouts(self, current_time: datetime) -> list[Tuple[str, datetime, Optional[datetime], Optional[float]]]:
        """Returns a list of timed-out landings: (aircraft_id, landing_time, dept_time, duration)."""
        timeouts = []
        for aid, state in self._states.items():
            if not state.confirmed_on_ground and state.last_seen:
                if (current_time - state.last_seen) > self._timeout_delta:
                    # Assumed landed due to signal dropout
                    state.confirmed_on_ground = True
                    state.pending_on_ground = True
                    
                    dept_time = state.departure_time
                    duration = None
                    landing_time = state.last_seen # Use the exact time of the last received beacon
                    
                    if dept_time:
                        duration = round((landing_time - dept_time).total_seconds() / 60.0, 1)
                        
                    state.departure_time = None
                    timeouts.append((aid, landing_time, dept_time, duration))
        return timeouts