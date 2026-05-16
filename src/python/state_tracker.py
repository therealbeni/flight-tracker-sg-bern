from dataclasses import dataclass
from typing import Optional

from models import Airport


@dataclass
class _AircraftState:
    confirmed_on_ground: bool
    pending_on_ground: bool
    pending_count: int


class AircraftStateTracker:
    """Tracks whether each aircraft is on ground or airborne near a given airport.

    Uses altitude relative to airport elevation with hysteresis and a
    confirmation count to avoid false transitions during ground rolls.
    """

    def __init__(
        self,
        airport: Airport,
        airborne_alt_m: float = 100.0,
        ground_alt_m: float = 70.0,
        confirm_beacons: int = 3,
    ):
        self._airport = airport
        self._airborne_alt_m = airborne_alt_m
        self._ground_alt_m = ground_alt_m
        self._confirm_beacons = confirm_beacons
        self._states: dict[str, _AircraftState] = {}

    def _classify(self, altitude: float, prev_on_ground: Optional[bool]) -> Optional[bool]:
        alt_above_airport = altitude - self._airport.elevation_m
        if alt_above_airport < self._ground_alt_m:
            return True
        if alt_above_airport > self._airborne_alt_m:
            return False
        return prev_on_ground  # hysteresis band: keep previous state

    def update(self, aircraft_id: str, altitude: Optional[float]) -> Optional[str]:
        """Process a new position beacon. Returns 'TAKEOFF', 'LANDING', or None."""
        if altitude is None:
            return None

        state = self._states.get(aircraft_id)

        if state is None:
            classification = self._classify(altitude, None)
            if classification is not None:
                self._states[aircraft_id] = _AircraftState(
                    confirmed_on_ground=classification,
                    pending_on_ground=classification,
                    pending_count=1,
                )
            return None

        current = self._classify(altitude, state.confirmed_on_ground)
        if current is None:
            return None

        if current == state.pending_on_ground:
            state.pending_count += 1
        else:
            state.pending_on_ground = current
            state.pending_count = 1

        if state.pending_count < self._confirm_beacons:
            return None
        if state.pending_on_ground == state.confirmed_on_ground:
            return None

        event = "LANDING" if state.pending_on_ground else "TAKEOFF"
        state.confirmed_on_ground = state.pending_on_ground
        return event
