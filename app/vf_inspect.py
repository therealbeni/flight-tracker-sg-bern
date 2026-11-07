"""Look at the club's flights in Vereinsflieger (read only), next to ours.

    docker compose exec app python vf_inspect.py 2026-10-03 [2026-10-10]

Logs in, reads the flights of the days (one call for the whole range) and
logs out: 5 calls. Prints to the terminal only - it shows members' names, so
don't paste the output into git.
"""

import sys
from datetime import date

from sqlalchemy import select

import vereinsflieger as vf
from database import SessionLocal
from models import Flight
from timeutil import local_day_bounds, to_local

FIELDS = ("flid", "dateofflight", "departuretime", "arrivaltime", "callsign", "pilotname", "uidpilot",
          "attendantname", "starttype", "ftid", "chargemode", "flidtow", "towcallsign", "towtime",
          "departurelocation", "arrivallocation", "flightmode", "uidcreate", "createtime", "deleted", "invoiced")


def main(first: date, last: date) -> None:
    db = SessionLocal()
    try:
        with vf.session(db) as client:
            user = client.user()
            print("Angemeldet als:", user.get("firstname"), user.get("lastname"), "uid", user.get("uid"),
                  "Rollen", user.get("roles"))
            flights = client.flights_between(first, last)
        print(f"\n{len(flights)} Flüge im Vereinsflieger {first} - {last}\n")
        print("\t".join(FIELDS))
        for f in flights:
            print("\t".join(str(f.get(k, "")) for k in FIELDS))
        start, _ = local_day_bounds(first)
        _, end = local_day_bounds(last)
        ours = db.scalars(select(Flight).where(Flight.deleted_at.is_(None), Flight.takeoff_time >= start,
                                               Flight.takeoff_time < end).order_by(Flight.takeoff_time)).all()
        print(f"\n{len(ours)} Flüge im Tracker\n")
        for f in ours:
            print(f.id, to_local(f.takeoff_time).strftime("%Y-%m-%d %H:%M"),
                  to_local(f.landing_time).strftime("%H:%M") if f.landing_time else "-", "UTC",
                  vf.utc_minutes(f.takeoff_time), f.glider.registration if f.glider else "?",
                  f.pilot_display, f.launch_method.value if f.launch_method else "-", f.flight_type,
                  f.takeoff_airfield_icao, f.landing_airfield_icao, "tow_flight", f.tow_flight_id, sep="\t")
        print("\nAnfragen heute:", vf.calls_today(db), "von", vf.DAILY_BUDGET)
    finally:
        db.close()


if __name__ == "__main__":
    a = date.fromisoformat(sys.argv[1])
    main(a, date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else a)
