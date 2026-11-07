"""The first write to the club's Vereinsflieger (stage 4, docs/vereinsflieger.md):
one test flight, read back, deleted again. Run only with Benja's go:

    docker compose run --rm --no-deps -e VEREINSFLIEGER_MODE=live app python vf_write_test.py --yes

The test flight: HB-3411 on 06.11.2026 (nobody flew), winch, Abrechnung "keine"
(can't be billed), Benja as pilot, marked TEST in the comment. If a test flight
from an earlier run is still there, it is deleted instead of making another.
Stops at the first surprise. About 9 calls.
"""

import json
import sys
from datetime import date

import vereinsflieger as vf
from database import SessionLocal

DAY = "2026-11-06"
COMMENT = "TEST Flugtracker - wird sofort gelöscht"
FLIGHT = {
    "callsign": "HB-3411",
    "uidpilot": 420398, "pilotname": "Bürgi, Benjamin",
    "starttype": "W",
    "departuretime": f"{DAY} 09:00",  # UTC = 10:00 local: the list should show 10:00
    "arrivaltime": f"{DAY} 09:05",
    "departurelocation": "Bern-Belp Gld LSZB",  # the exact name
    "arrivallocation": "LSZB",  # ICAO only: which of the two Bern entries does it pick?
    "landingcount": 1,
    "ftid": 10,
    "chargemode": 1,  # keine
    "comment": COMMENT,
}
SHOW = ("flid", "callsign", "dateofflight", "departuretime", "arrivaltime", "flighttime", "pilotname", "uidpilot",
        "starttype", "ftid", "chargemode", "departurelocation", "arrivallocation", "flightmode", "comment",
        "uidcreate", "deleted", "invoiced", "flidtow")


def show(title, flight):
    print(title, json.dumps({k: flight.get(k) for k in SHOW}, ensure_ascii=False))


def tests_of(flights):
    return [f for f in flights if f.get("comment") == COMMENT and f.get("deleted") in ("0", 0, None)]


def main():
    if "--yes" not in sys.argv:
        sys.exit("Schreibt in den echten Vereinsflieger - nur mit --yes.")
    db = SessionLocal()
    try:
        with vf.session(db) as client:
            before = client.flights_of_day(date.fromisoformat(DAY))
            print(f"{DAY}: {len(before)} Flüge vorher")
            for f in before:
                show("  vorher", f)
            if any(f.get("deleted") in ("0", 0, None) and not tests_of([f]) for f in before):
                sys.exit("Am Testtag gibt es echte Flüge - abgebrochen.")
            leftovers = tests_of(before)
            if leftovers:
                for f in leftovers:
                    client.delete_flight(int(f["flid"]))
                    print("Alter Testflug gelöscht:", f["flid"])
                return
            created = client.add_flight(FLIGHT)
            show("Antwort add", created)
            flid = created.get("flid")
            if not flid:
                found = tests_of(client.flights_of_day(date.fromisoformat(DAY)))
                if len(found) != 1:
                    sys.exit(f"Keine flid in der Antwort und {len(found)} Testflüge gefunden - bitte von Hand prüfen.")
                flid = found[0]["flid"]
            show("get", client.flight(int(flid)))
            client.delete_flight(int(flid))
            print("gelöscht:", flid)
            after = client.flights_of_day(date.fromisoformat(DAY))
            for f in after:
                show("  nachher", f)
            if tests_of(after):
                sys.exit("Testflug ist nach dem Löschen noch da - bitte von Hand prüfen!")
            print("OK: angelegt, gelesen, gelöscht.")
        print("Anfragen heute:", vf.calls_today(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
