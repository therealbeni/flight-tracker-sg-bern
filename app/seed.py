"""One-off seed of the club's known gliders and home airfields.

Run once after the database is up: `docker compose run --rm app python seed.py`
Safe to re-run - existing rows are left untouched, only missing ones are added.
"""

from sqlalchemy import select

from database import SessionLocal
from models import AircraftKind, Airfield, Glider

# Initial fleet (OGN device id -> registration). After seeding, the `gliders`
# table is the source of truth - the tracker reads the fleet from there.
SG_BERN_FLEET = {
    "3D0EB4": "D-EDUY",
    "4B473F": "HB-664",
    "4B4B8D": "HB-1766",
    "4B4BBA": "HB-1811",
    "4B4DF0": "HB-2377",
    "4B50E2": "HB-3131",
    "4B5177": "HB-3280",
    "4B51C9": "HB-3362",
    "4B51FA": "HB-3411",
    "4B521E": "HB-3447",
    "4B5224": "HB-3453",
}

# Everything else is a glider.
FLEET_KINDS = {"D-EDUY": AircraftKind.TOWPLANE, "HB-2377": AircraftKind.MOTORGLIDER}

# Further airfields are added by the tracker automatically when a club glider
# lands there.
HOME_AIRFIELDS = [
    Airfield(icao="LSZB", name="Bern Belp", latitude=46.9144, longitude=7.4990, elevation_m=510.0),
    Airfield(icao="LSTZ", name="Zweisimmen", latitude=46.551713, longitude=7.381012, elevation_m=935.0),
]


def run():
    db = SessionLocal()
    try:
        for device_id, registration in SG_BERN_FLEET.items():
            exists = db.scalar(select(Glider).where(Glider.registration == registration))
            if not exists:
                kind = FLEET_KINDS.get(registration, AircraftKind.GLIDER)
                db.add(Glider(registration=registration, ogn_device_id=device_id, kind=kind))
                print(f"Added glider {registration}")

        for airfield in HOME_AIRFIELDS:
            if not db.get(Airfield, airfield.icao):
                db.add(airfield)
                print(f"Added airfield {airfield.icao}")

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()
