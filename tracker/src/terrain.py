"""Terrain elevation (SRTM) for computing height above ground.

srtm.py downloads 1x1 degree tiles on first use. A download takes seconds (or
times out), and APRS servers disconnect clients that stop reading, so tiles
are never downloaded on the beacon-processing path: an unknown tile is queued
for a background download and elevation() returns None until it's there
(the detector copes without terrain, see FlightDetector._height). Failed
downloads are retried after a while, not on every beacon.

The cache directory should be persistent (compose.yaml mounts it), and
preload() fetches the home region at startup.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import Optional

import srtm

DEFAULT_CACHE_DIR = os.environ.get("SRTM_CACHE_DIR", "")


class Terrain:
    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, retry_after_s: float = 1800.0,
                 background: bool = True):
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        self._data = srtm.get_data(local_cache_dir=cache_dir, timeout=30)
        self._retry_after_s = retry_after_s
        self._background = background
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._retry_at: dict[str, float] = {}
        if background:
            threading.Thread(target=self._download_loop, name="srtm-download", daemon=True).start()

    def elevation(self, lat: float, lon: float) -> Optional[float]:
        tile = self._data.get_file_name(lat, lon)
        if tile is None:
            return None  # no SRTM coverage here
        if tile not in self._data.files:
            if not self._background:
                self._load(tile, lat, lon)
            else:
                self._request(tile, lat, lon)
            if tile not in self._data.files:
                return None
        try:
            value = self._data.get_elevation(lat, lon)
        except Exception as exc:  # noqa: BLE001 - corrupt tile etc. must not stop tracking
            print(f"Terrain lookup failed at {lat:.3f},{lon:.3f}: {exc}", file=sys.stderr)
            return None
        # SRTM marks voids with large negative numbers.
        if value is None or value < -500:
            return None
        return float(value)

    def preload(self, lat_min: int, lat_max: int, lon_min: int, lon_max: int) -> None:
        """Download all tiles in the box now (blocking), e.g. at startup."""
        for lat in range(lat_min, lat_max + 1):
            for lon in range(lon_min, lon_max + 1):
                tile = self._data.get_file_name(lat + 0.5, lon + 0.5)
                if tile is not None and tile not in self._data.files:
                    self._load(tile, lat + 0.5, lon + 0.5)

    def _request(self, tile: str, lat: float, lon: float) -> None:
        if tile in self._pending or self._retry_at.get(tile, 0.0) > time.monotonic():
            return
        self._pending.add(tile)
        self._queue.put((tile, lat, lon))

    def _download_loop(self) -> None:
        while True:
            tile, lat, lon = self._queue.get()
            try:
                self._load(tile, lat, lon)
            finally:
                self._pending.discard(tile)

    def _load(self, tile: str, lat: float, lon: float) -> None:
        if self._retry_at.get(tile, 0.0) > time.monotonic():
            return
        try:
            loaded = self._data.get_file(lat, lon) is not None
        except Exception as exc:  # noqa: BLE001 - network errors
            print(f"Terrain tile {tile} unavailable: {exc}", file=sys.stderr)
            loaded = False
        if not loaded:
            self._retry_at[tile] = time.monotonic() + self._retry_after_s
