from dataclasses import dataclass
from enum import Enum


class FlightState(Enum):
    GROUND = 0
    FLYING = 1


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
class AircraftInfo:
    registration: str
    model: str
    

@dataclass
class Airport:
    icao: str
    name: str
    lat: float
    lon: float
    elevation_m: float


@dataclass
class FlightPhaseRules:
    takeoff_speed_min: float = 60.0  # km/h
    takeoff_agl_min: float = 20.0    # meters AGL