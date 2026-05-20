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
    takeoff_agl_min: float = 30.0    # meters AGL


AircraftFleet = dict[str, str]  # OGN device_id -> registration

SG_BERN_FLEET = {
    '3D0EB4': 'D-EDUY',
    '4B473F': 'HB-664',
    '4B4B8D': 'HB-1766',
    '4B4BBA': 'HB-1811',
    '4B4DF0': 'HB-2377',
    '4B50E2': 'HB-3131',
    '4B5177': 'HB-3280',
    '4B51C9': 'HB-3362',
    '4B51FA': 'HB-3411',
    '4B521E': 'HB-3447',
    '4B5224': 'HB-3453',
}