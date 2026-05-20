import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Union
import srtm

from models import AIRCRAFT_TYPE_NAMES, Airport, FlightPhaseRules, FlightState
from flight_record import FlightRecord
from ddb import DeviceDatabase


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))



class GlobalFlightTracker:
    """Takes in OGN Data and creates a flight_record for each plane and keeps it in memory.
    Uses the airports.csv to detect if a plane lands at an airport and then logs accordingly
    """

    def __init__(
        self,
        airports_csv: Union[str, Path],
        ddb: DeviceDatabase,
        phase_rules: Optional[FlightPhaseRules] = None,
        detection_radius_km: float = 5.0,
    ):
        self._ddb = ddb
        self._phase_rules = phase_rules or FlightPhaseRules()
        self._detection_radius_km = detection_radius_km

        self._airports: list[Airport] = self._load_airports(Path(airports_csv))
        self._active_flights: dict[str, FlightRecord] = {}

        self.elevation_data = srtm.get_data()

    def _load_airports(self, path: Path) -> list[Airport]:
        airports = []
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (ValueError, KeyError):
                    continue
                elevation_ft = row.get("elevation_ft", "")
                elevation_m = float(elevation_ft) * 0.3048 if elevation_ft else 0.0
                icao = row.get("icao_code") or row.get("ident") or ""
                name = row.get("name", "")
                airports.append(Airport(icao=icao, name=name, lat=lat, lon=lon, elevation_m=elevation_m))
        return airports

    def _nearest_airport(self, lat: float, lon: float) -> Optional[Airport]:
        best = None
        best_dist = self._detection_radius_km
        for airport in self._airports:
            dist = _haversine(lat, lon, airport.lat, airport.lon)
            if dist < best_dist:
                best_dist = dist
                best = airport
        return best

    def _classify(self, speed: float, altitude_msl, lat, lon) -> FlightState:
        # compute agl for current point to avoid errors with thermaling or helicopters
        try:
            terrain = self.elevation_data.get_elevation(lat, lon)
        except TypeError as err:
            print("Elevation data failed due to {err}")
            return FlightState.GROUND # Default return 
        
        agl = altitude_msl - terrain
        if speed < self._phase_rules.takeoff_speed_min and agl < self._phase_rules.takeoff_agl_min:
            return FlightState.GROUND
        else:
            return FlightState.FLYING

    def process_beacon(self, beacon: dict) -> Optional[FlightRecord]:
        if beacon.get("aprs_type") != "position":
            return None
        
        latitude = beacon.get("latitude")
        longitude = beacon.get("longitude")
        altitude = beacon.get("altitude")
        speed = beacon.get("ground_speed")
        
        if latitude is None or longitude is None or altitude is None or speed is None:
            return None # Invalid data
        
        # prepare data
        plane_id = beacon.get("name", "")
        address = beacon.get("address", "")
        ddb_entry = self._ddb.lookup(address)
        callsign = ddb_entry.registration if ddb_entry else ""
        plane_type = (
            ddb_entry.model if ddb_entry and ddb_entry.model
            else AIRCRAFT_TYPE_NAMES.get(beacon.get("aircraft_type") or 0, "Unknown")
        )
        vertical_speed = beacon.get("climb_rate", "")
        flight_state = self._classify(speed, altitude, latitude, longitude)
        timestamp = beacon.get("timestamp")

        if plane_id not in self._active_flights:
            # plane is new, no active flight exists
            new_flight = FlightRecord(
                plane_id,
                plane_type,
                callsign,
                flight_state,
                timestamp)
            new_flight.update(latitude, longitude, altitude, speed, vertical_speed, timestamp)
            self._active_flights[plane_id] = new_flight
            return new_flight

        else:
            # update entry
            flight = self._active_flights[plane_id]
            flight.update(latitude,
                          longitude,
                          altitude,
                          speed,
                          vertical_speed,
                          timestamp)
            old_flight_state = flight.flight_state

            if old_flight_state == flight_state:
                # no change
                return flight
            
            elif old_flight_state == FlightState.GROUND and flight_state == FlightState.FLYING:
                # Takeoff
                # get nearest airport
                airport = self._nearest_airport(latitude, longitude)
                flight.takeoff(airport)
                return flight
            
            elif old_flight_state == FlightState.FLYING and flight_state == FlightState.GROUND:
                # Land
                airport = self._nearest_airport(latitude, longitude)
                flight.land(airport)
                self._active_flights.pop(plane_id)
                return flight


CSV_FIELDNAMES = [
    "record_id", "plane_id", "callsign", "plane_type",
    "latitude", "longitude", "altitude_m", "speed_kmh",
    "takeoff_airport", "landing_airport",
    "takeoff_time", "landing_time", "flight_duration_min",
]


class AirportLogger:
    """Logs all traffic related to a specific airport as icao_movements_date.csv.

    Each row represents one flight. The row is written on takeoff and overwritten
    on landing so landing data is filled in on the same record.
    """

    def __init__(self, airport: Airport, output_dir: Union[str, Path] = "."):
        self.airport = airport
        self._output_dir = Path(output_dir)
        self._current_date: Optional[date] = None
        self._rows: dict[str, dict] = {}
        self._rotate()

    def _daily_path(self) -> Path:
        return self._output_dir / f"{self.airport.icao}_movements_{date.today()}.csv"

    def _rotate(self) -> None:
        today = date.today()
        if self._current_date == today:
            return
        self._current_date = today
        self._rows = {}
        path = self._daily_path()
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("record_id"):
                        self._rows[row["record_id"]] = row

    def _flush(self) -> None:
        with self._daily_path().open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(self._rows.values())

    def log(self, flight_record: FlightRecord) -> None:
        takeoff_icao = flight_record.takeoff_airport.icao if flight_record.takeoff_airport else None
        landing_icao = flight_record.landing_airport.icao if flight_record.landing_airport else None

        # Check if this flight is relevant to this specific airport logger
        if takeoff_icao != self.airport.icao and landing_icao != self.airport.icao:
            return

        self._rotate()

        duration = flight_record.flight_duration
        duration_min = round(duration.total_seconds() / 60, 1) if duration is not None else ""

        # Using unique UUID record_id prevents overwriting separate flight legs!
        self._rows[flight_record.record_id] = {
            "record_id": flight_record.record_id,
            "plane_id": flight_record.plane_id,
            "callsign": flight_record.callsign,
            "plane_type": flight_record.plane_type,
            "latitude": flight_record.latitude if flight_record.latitude is not None else "",
            "longitude": flight_record.longitude if flight_record.longitude is not None else "",
            "altitude_m": flight_record.height if flight_record.height is not None else "",
            "speed_kmh": flight_record.speed if flight_record.speed is not None else "",
            "takeoff_airport": takeoff_icao,
            "landing_airport": landing_icao,
            "takeoff_time": flight_record.takeoff_time.isoformat() if flight_record.takeoff_time else "",
            "landing_time": flight_record.landing_time.isoformat() if flight_record.landing_time else "",
            "flight_duration_min": duration_min,
        }
        self._flush()

    def close(self) -> None:
        pass
