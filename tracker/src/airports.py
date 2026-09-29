"""Airfield lookup: "which airfield is this position at?"

Backed by tracker/src/airports.csv, a standard export from OurAirports
(https://ourairports.com/data/). Only real airfields are used: heliports,
closed airfields, seaplane bases and balloon ports are skipped, otherwise a
hospital helipad next to a runway could be picked instead of the airfield.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

USABLE_TYPES = {"small_airport", "medium_airport", "large_airport"}


@dataclass(frozen=True)
class Airport:
    icao: str  # ICAO code, or the OurAirports ident (e.g. "CH-0012") if it has none
    name: str
    lat: float
    lon: float
    elevation_m: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class AirportDirectory:
    """Finds the nearest airfield within `radius_km` of a position.

    The radius is measured from the airfield's reference point, which is
    usually near the middle of the runway - 3 km covers both runway ends of
    fields like Bern (1.7 km runway) without swallowing nearby outlanding fields.
    """

    def __init__(self, airports: Iterable[Airport], radius_km: float = 3.0):
        self.radius_km = radius_km
        self._airports = list(airports)
        # Coarse 1x1 degree grid so a lookup only checks nearby airfields.
        self._grid: dict[tuple[int, int], list[Airport]] = {}
        for a in self._airports:
            self._grid.setdefault((math.floor(a.lat), math.floor(a.lon)), []).append(a)

    @classmethod
    def from_csv(cls, path: Union[str, Path], radius_km: float = 3.0) -> "AirportDirectory":
        airports = []
        with Path(path).open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("type") not in USABLE_TYPES:
                    continue
                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (ValueError, KeyError):
                    continue
                elevation_ft = row.get("elevation_ft") or ""
                try:
                    elevation_m = float(elevation_ft) * 0.3048 if elevation_ft else 0.0
                except ValueError:
                    elevation_m = 0.0
                code = row.get("icao_code") or row.get("gps_code") or row.get("ident") or ""
                if not code:
                    continue
                airports.append(Airport(icao=code.upper(), name=row.get("name", ""), lat=lat, lon=lon, elevation_m=elevation_m))
        return cls(airports, radius_km)

    def __len__(self) -> int:
        return len(self._airports)

    def get(self, icao: str) -> Optional[Airport]:
        icao = icao.upper()
        return next((a for a in self._airports if a.icao == icao), None)

    def nearest(self, lat: float, lon: float) -> Optional[Airport]:
        best, best_dist = None, self.radius_km
        cell_lat, cell_lon = math.floor(lat), math.floor(lon)
        for d_lat in (-1, 0, 1):
            for d_lon in (-1, 0, 1):
                for a in self._grid.get((cell_lat + d_lat, cell_lon + d_lon), ()):
                    dist = haversine_km(lat, lon, a.lat, a.lon)
                    if dist < best_dist:
                        best, best_dist = a, dist
        return best
