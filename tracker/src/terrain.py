"""Terrain elevation (SRTM) for computing height above ground.

srtm.py downloads 1x1 degree tiles on first use. Downloading happens inline
(it blocks beacon processing for a few seconds), so the cache directory should
be persistent - compose.yaml mounts it as a volume - and `preload()` fetches
the tiles for our region at startup instead of in the middle of a flight.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import srtm

DEFAULT_CACHE_DIR = os.environ.get("SRTM_CACHE_DIR", "")


class Terrain:
    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR):
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        self._data = srtm.get_data(local_cache_dir=cache_dir)

    def elevation(self, lat: float, lon: float) -> Optional[float]:
        try:
            value = self._data.get_elevation(lat, lon)
        except Exception as exc:  # noqa: BLE001 - network/tile errors must not stop tracking
            print(f"Terrain lookup failed at {lat:.3f},{lon:.3f}: {exc}", file=sys.stderr)
            return None
        # SRTM marks voids with large negative numbers.
        if value is None or value < -500:
            return None
        return float(value)

    def preload(self, lat_min: int, lat_max: int, lon_min: int, lon_max: int) -> None:
        """Make sure all tiles in the box are downloaded (e.g. at startup)."""
        for lat in range(lat_min, lat_max + 1):
            for lon in range(lon_min, lon_max + 1):
                self.elevation(lat + 0.5, lon + 0.5)
