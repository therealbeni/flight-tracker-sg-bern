"""Starts the web app on a throwaway SQLite database with realistic data, for
the browser walk-through (dev/ui/run.sh). Runs inside the app image.

Also writes the fake camera video for the QR scanner test: a Y4M file showing
the QR code of HB-1811, which Chromium plays as its "camera".
"""

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:////tmp/ui.db"
os.environ.setdefault("SESSION_SECRET", "ui-test")
sys.path[:0] = ["/app", "/repo"]

import qrcode  # noqa: E402
import uvicorn  # noqa: E402
from PIL import Image  # noqa: E402

from database import Base, SessionLocal, engine  # noqa: E402
from models import (AircraftKind, Airfield, Flight, FlightSource, Glider, GliderClaim, LaunchMethod,  # noqa: E402
                    Pilot, PilotRole, PilotStatus)
from security import hash_password  # noqa: E402

PASSWORD = "testtest"
HB1811_TOKEN = "11111111-2222-3333-4444-555555555555"
BASE_URL = os.environ["BASE_URL"]


def seed() -> None:
    if os.path.exists("/tmp/ui.db"):
        os.remove("/tmp/ui.db")
    Base.metadata.create_all(engine)
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    db.add_all([
        Airfield(icao="LSZB", name="Bern Belp", latitude=46.9144, longitude=7.499, elevation_m=510),
        Airfield(icao="LSTZ", name="Zweisimmen", latitude=46.5517, longitude=7.381, elevation_m=935),
    ])
    people = {
        "admin": Pilot(full_name="Anna Admin", email="admin@test.ch", role=PilotRole.ADMIN),
        "pilot": Pilot(full_name="Pia Pilot", email="pilot@test.ch", role=PilotRole.PILOT),
        "tow": Pilot(full_name="Toni Schlepp", email="tow@test.ch", role=PilotRole.PILOT),
        "desk": Pilot(full_name="Flugdienstleiter LSZB", email="desk@test.ch", role=PilotRole.FDL),
    }
    for p in people.values():
        p.password_hash, p.status = hash_password(PASSWORD), PilotStatus.APPROVED
    db.add(Pilot(full_name="Neu Ling", email="new@test.ch", password_hash=hash_password(PASSWORD),
                 role=PilotRole.PILOT, status=PilotStatus.PENDING))
    fleet = {
        "HB-1811": Glider(registration="HB-1811", model="ASK 21", ogn_device_id="4B4BBA", claim_token=HB1811_TOKEN),
        "HB-3131": Glider(registration="HB-3131", model="LS 4", ogn_device_id="4B50E2"),
        "HB-2377": Glider(registration="HB-2377", model="H36 Dimona", ogn_device_id="4B4DF0",
                          kind=AircraftKind.MOTORGLIDER),
        "D-EDUY": Glider(registration="D-EDUY", model="Robin DR400", ogn_device_id="3D0EB4",
                         kind=AircraftKind.TOWPLANE),
        # Pia's own glider: only she is offered it.
        "HB-3407": Glider(registration="HB-3407", model="LS 8", ogn_device_id="4B5407", owners=[people["pilot"]]),
    }
    db.add_all([*people.values(), *fleet.values()])
    db.flush()
    db.add(GliderClaim(glider_id=fleet["D-EDUY"].id, pilot_id=people["tow"].id, claimed_at=now - timedelta(hours=3),
                       expires_at=now + timedelta(hours=8), whole_day=True))
    t = now - timedelta(hours=2)
    tow = Flight(record_id="tow1", glider_id=fleet["D-EDUY"].id, pilot_id=people["tow"].id, takeoff_time=t,
                 landing_time=t + timedelta(minutes=9), duration_min=9, takeoff_airfield_icao="LSZB",
                 landing_airfield_icao="LSZB", launch_method=LaunchMethod.SELF, flight_type="F", billing="none",
                 source=FlightSource.AUTO)
    db.add(tow)
    db.flush()
    db.add_all([
        Flight(record_id="g1", glider_id=fleet["HB-1811"].id, pilot_id=people["pilot"].id, takeoff_time=t,
               landing_time=t + timedelta(minutes=47), duration_min=47, takeoff_airfield_icao="LSZB",
               landing_airfield_icao="LSZB", launch_method=LaunchMethod.AEROTOW, tow_flight_id=tow.id,
               source=FlightSource.AUTO),
        Flight(record_id="g2", glider_id=fleet["HB-3131"].id, takeoff_time=now - timedelta(minutes=35),
               takeoff_airfield_icao="LSZB", source=FlightSource.AUTO),
        Flight(record_id="m1", glider_id=fleet["HB-2377"].id, pilot_id=people["pilot"].id,
               takeoff_time=now - timedelta(hours=5), landing_time=now - timedelta(hours=4, minutes=20),
               duration_min=40, takeoff_airfield_icao="LSZB", landing_latitude=46.95, landing_longitude=7.7,
               landing_estimated=True, launch_method=LaunchMethod.SELF, source=FlightSource.AUTO),
    ])
    # Earlier flying days, for the calendar.
    for days_ago, hour in [(3, 11), (3, 13), (9, 12), (16, 14)]:
        start = now.replace(hour=hour, minute=5) - timedelta(days=days_ago)
        db.add(Flight(record_id=f"old-{days_ago}-{hour}", glider_id=fleet["HB-1811"].id, pilot_id=people["pilot"].id,
                      takeoff_time=start, landing_time=start + timedelta(minutes=30), duration_min=30,
                      takeoff_airfield_icao="LSZB", landing_airfield_icao="LSZB", launch_method=LaunchMethod.WINCH,
                      source=FlightSource.AUTO))
    db.commit()


def write_fake_camera(path: str) -> None:
    """A few seconds of 'camera' looking at HB-1811's QR code (Y4M, 4:2:0)."""
    code = qrcode.make(f"{BASE_URL}/claim/{HB1811_TOKEN}").convert("L").resize((360, 360))
    frame = Image.new("L", (640, 480), 200)
    frame.paste(code, (140, 60))
    y = frame.tobytes()
    chroma = bytes([128]) * (320 * 240)
    with open(path, "wb") as f:
        f.write(b"YUV4MPEG2 W640 H480 F10:1 Ip A1:1 C420jpeg\n")
        for _ in range(30):
            f.write(b"FRAME\n" + y + chroma + chroma)


if __name__ == "__main__":
    seed()
    write_fake_camera("/out/qr-camera.y4m")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="warning")
