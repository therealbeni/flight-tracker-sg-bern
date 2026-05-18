from dataclasses import dataclass
from datetime import datetime
from typing import Optional


AIRCRAFT_TYPE_NAMES = {
    0: "Unknown",
    1: "Glider",
    2: "Tow plane",
    3: "Helicopter",
    4: "Skydiver",
    5: "Drop plane",
    6: "Hang glider",
    7: "Paraglider",
    8: "Powered aircraft",
    9: "Jet aircraft",
    10: "UFO",
    11: "Balloon",
    12: "Airship",
    13: "UAV",
    14: "Ground vehicle",
    15: "Static object",
}


@dataclass
class Airport:
    icao: str
    name: str
    lat: float
    lon: float
    elevation_m: float


@dataclass
class AircraftInfo:
    registration: str
    model: str


@dataclass
class FlightEvent:
    timestamp: datetime
    event: str  # "TAKEOFF" or "LANDING"
    aircraft_id: str
    callsign: str
    address: str
    aircraft_type: str
    latitude: float
    longitude: float
    altitude_m: Optional[float]
    ground_speed_kmh: Optional[float]
    climb_rate_ms: float
    receiver: str
    departure_time: Optional[datetime] = None
    flight_duration_min: Optional[float] = None
