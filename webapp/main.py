import csv
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Flight Tracker LSZB")

_PROJECT_ROOT = Path(__file__).parent.parent
_CSV_BASE = _PROJECT_ROOT / "lszb_movements.csv"


class FlightEvent(BaseModel):
    timestamp: datetime
    event: str
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
    departure_time: Optional[datetime]
    flight_duration_min: Optional[float]


def _csv_paths_for_date(target_date: date) -> list[Path]:
    daily = _CSV_BASE.with_name(f"{_CSV_BASE.stem}_{target_date}{_CSV_BASE.suffix}")
    paths = []
    if daily.exists():
        paths.append(daily)
    if _CSV_BASE.exists():
        paths.append(_CSV_BASE)
    return paths


def _load_events(paths: list[Path]) -> list[FlightEvent]:
    seen_keys: set[tuple] = set()
    events: list[FlightEvent] = []

    for path in paths:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                key = (row["timestamp"], row["aircraft_id"], row["event"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                events.append(FlightEvent(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    event=row["event"],
                    aircraft_id=row["aircraft_id"],
                    callsign=row["callsign"],
                    address=row["address"],
                    aircraft_type=row["aircraft_type"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    altitude_m=float(row["altitude_m"]) if row["altitude_m"] else None,
                    ground_speed_kmh=float(row["ground_speed_kmh"]) if row["ground_speed_kmh"] else None,
                    climb_rate_ms=float(row["climb_rate_ms"]),
                    receiver=row["receiver"],
                    departure_time=datetime.fromisoformat(row["departure_time"]) if row["departure_time"] else None,
                    flight_duration_min=float(row["flight_duration_min"]) if row["flight_duration_min"] else None,
                ))

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events


def _all_events() -> list[FlightEvent]:
    paths = []
    for csv_file in sorted(_PROJECT_ROOT.glob(f"{_CSV_BASE.stem}_*.csv")):
        paths.append(csv_file)
    if _CSV_BASE.exists():
        paths.append(_CSV_BASE)
    return _load_events(paths)


@app.get("/")
def root():
    return {"status": "ok", "airport": "LSZB"}


@app.get("/flights", response_model=list[FlightEvent])
def list_flights(
    event: Optional[str] = Query(None, description="Filter by event type: TAKEOFF or LANDING"),
    aircraft_type: Optional[str] = Query(None, description="Filter by aircraft type"),
    date_from: Optional[date] = Query(None, description="Include events on or after this date (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Include events on or before this date (YYYY-MM-DD)"),
    limit: int = Query(100, ge=1, le=1000),
):
    events = _all_events()
    if event:
        events = [e for e in events if e.event.upper() == event.upper()]
    if aircraft_type:
        events = [e for e in events if aircraft_type.lower() in e.aircraft_type.lower()]
    if date_from:
        events = [e for e in events if e.timestamp.date() >= date_from]
    if date_to:
        events = [e for e in events if e.timestamp.date() <= date_to]
    return events[:limit]


@app.get("/flights/today", response_model=list[FlightEvent])
def flights_today():
    today = date.today()
    paths = _csv_paths_for_date(today)
    if not paths:
        return []
    events = _load_events(paths)
    return [e for e in events if e.timestamp.date() == today]


@app.get("/flights/{aircraft_id}", response_model=list[FlightEvent])
def flights_for_aircraft(aircraft_id: str):
    events = _all_events()
    matches = [e for e in events if e.aircraft_id.upper() == aircraft_id.upper()
               or e.callsign.upper() == aircraft_id.upper()]
    if not matches:
        raise HTTPException(status_code=404, detail=f"No flights found for {aircraft_id}")
    return matches


@app.get("/summary")
def summary():
    events = _all_events()
    takeoffs = [e for e in events if e.event == "TAKEOFF"]
    landings = [e for e in events if e.event == "LANDING"]
    completed = [e for e in landings if e.flight_duration_min is not None]
    aircraft_ids = {e.aircraft_id for e in events}
    avg_duration = (
        round(sum(e.flight_duration_min for e in completed) / len(completed), 1)
        if completed else None
    )
    return {
        "total_events": len(events),
        "takeoffs": len(takeoffs),
        "landings": len(landings),
        "completed_flights": len(completed),
        "unique_aircraft": len(aircraft_ids),
        "avg_flight_duration_min": avg_duration,
    }
