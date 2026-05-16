import csv
from datetime import date
from pathlib import Path
from typing import Union

from models import FlightEvent

CSV_FIELDNAMES = [
    "timestamp", "event", "aircraft_id", "callsign", "address", "aircraft_type",
    "latitude", "longitude", "altitude_m", "ground_speed_kmh", "climb_rate_ms",
    "receiver", "departure_time", "flight_duration_min",
]


class CSVLogger:
    def __init__(self, base_path: Union[str, Path]):
        self._base_path = Path(base_path)
        self._current_date: date | None = None
        self._file = None
        self._writer = None
        self._rotate()

    def _daily_path(self) -> Path:
        return self._base_path.with_name(
            f"{self._base_path.stem}_{date.today()}{self._base_path.suffix}"
        )

    def _rotate(self) -> None:
        today = date.today()
        if self._current_date == today:
            return
        if self._file:
            self._file.close()
        self._current_date = today
        path = self._daily_path()
        self._file = path.open("a", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_FIELDNAMES)
        if self._file.tell() == 0:
            self._writer.writeheader()

    def log(self, event: FlightEvent) -> None:
        self._rotate()
        row = {
            "timestamp": event.timestamp.isoformat(),
            "event": event.event,
            "aircraft_id": event.aircraft_id,
            "callsign": event.callsign,
            "address": event.address,
            "aircraft_type": event.aircraft_type,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "altitude_m": event.altitude_m if event.altitude_m is not None else "",
            "ground_speed_kmh": event.ground_speed_kmh if event.ground_speed_kmh is not None else "",
            "climb_rate_ms": event.climb_rate_ms,
            "receiver": event.receiver,
            "departure_time": event.departure_time.isoformat() if event.departure_time else "",
            "flight_duration_min": event.flight_duration_min if event.flight_duration_min is not None else "",
        }
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
