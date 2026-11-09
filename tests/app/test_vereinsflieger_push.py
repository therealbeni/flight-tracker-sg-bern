"""Tag abschliessen sends the day to Vereinsflieger (mode "live"), against a
fake Vereinsflieger: only when every flight is green, each flight once."""

from datetime import datetime, timedelta, timezone

import pytest

import vereinsflieger as vf
import vereinsflieger_push as push
from models import Flight, FlightAuditEntry, LaunchMethod, VereinsfliegerEntry
from test_flugbuch import add_flight, login, today_at, world  # noqa: F401 - world is a fixture
from timeutil import today_local


class FakeVereinsflieger:
    """Keeps the flights added; `refuse` / `lose_answer`: the next flight/add
    is refused / gets no answer (but was created)."""

    def __init__(self):
        self.flights = []
        self.requests = []
        self.refuse = self.lose_answer = False

    def __call__(self, method, url, fields):
        endpoint = url.removeprefix(vf.BASE_URL)
        self.requests.append((endpoint, dict(fields)))
        if endpoint == "auth/accesstoken":
            return vf.Response(200, {"accesstoken": "TOKEN"})
        if endpoint == "flight/list/date":
            return vf.Response(200, {**{str(i): f for i, f in enumerate(self.flights)}, "httpstatuscode": 200})
        if endpoint == "flight/add":
            if self.refuse:
                self.refuse = False
                return vf.Response(400, {"error": "Startart nicht erlaubt"})
            flight = {**fields, "flid": str(9000 + len(self.flights)), "deleted": "0"}
            self.flights.append(flight)
            if self.lose_answer:
                self.lose_answer = False
                raise OSError("timeout")
            return vf.Response(200, {**flight, "httpstatuscode": 200})
        return vf.Response(200, {"httpstatuscode": 200})

    def sent(self, endpoint="flight/add"):
        return [fields for e, fields in self.requests if e.startswith(endpoint)]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeVereinsflieger()
    monkeypatch.setattr(vf, "http_transport", fake)
    for name, value in [("MODE", "live"), ("APPKEY", "KEY"), ("USER", "fdl"), ("PASSWORD", "geheim")]:
        monkeypatch.setenv(f"VEREINSFLIEGER_{name}", value)
    return fake


@pytest.fixture
def linked(db_session, world):
    """Pia and Bob linked to their Vereinsflieger members; aircraft and Bern known."""
    seen = today_local()
    db_session.add_all([VereinsfliegerEntry(kind="member", key="501", name="Pilot, Pia", flights=3, last_flight=seen),
                        VereinsfliegerEntry(kind="member", key="503", name="Brunner, Bob", flights=3, last_flight=seen),
                        VereinsfliegerEntry(kind="aircraft", key="HB-1811", flights=3, last_flight=seen),
                        VereinsfliegerEntry(kind="aircraft", key="D-EDUY", flights=3, last_flight=seen),
                        VereinsfliegerEntry(kind="place", key="Bern-Belp Gld LSZB", flights=9, last_flight=seen),
                        VereinsfliegerEntry(kind="place", key="Bern-Belp LSZB", flights=1, last_flight=seen)])
    world["pia"].vereinsflieger_member_id, world["bob"].vereinsflieger_member_id = "501", "503"
    db_session.commit()
    return world


def green_flight(db, world, start="11:00", end="11:45", **kwargs):
    kwargs.setdefault("launch_method", LaunchMethod.WINCH)
    return add_flight(db, world["glider"], world["pia"], start, end, verified_by_pilot=True, **kwargs)


def close(client, day=None):
    return client.post("/flugbuch/abschliessen", data={"day": str(day or today_local())}, follow_redirects=True)


def test_day_with_a_flight_not_green_is_not_closed_or_sent(client, db_session, linked, fake):
    green_flight(db_session, linked)
    add_flight(db_session, linked["glider"], linked["pia"], "13:00", "13:30", launch_method=LaunchMethod.WINCH)
    login(client, "desk")
    page = client.get("/flugbuch").text
    assert "Tag abschliessen und übertragen</button>" in page and "disabled>Tag abschliessen" in page
    resp = close(client)
    assert resp.status_code == 409 and "erst wenn alle Flüge grün sind" in resp.text
    assert fake.requests == []
    assert all(f.finalized_at is None for f in db_session.query(Flight))


def test_closing_a_green_day_sends_each_flight(client, db_session, linked, fake):
    flight = green_flight(db_session, linked, notes="Thermik")
    login(client, "desk")
    page = client.get("/flugbuch").text
    assert "Alle Flüge sind bereit" in page and "1 Flug wird dort eingetragen" in page
    resp = close(client)
    assert "1 Flug an den Vereinsflieger übertragen" in resp.text
    [sent] = fake.sent()
    day = today_local().isoformat()
    assert sent == {"callsign": "HB-1811", "departuretime": f"{day} 11:00", "arrivaltime": f"{day} 11:45",
                    "departurelocation": "Bern-Belp Gld LSZB", "arrivallocation": "Bern-Belp Gld LSZB",
                    "landingcount": 1, "starttype": 5, "ftid": 10, "chargemode": 2, "uidpilot": "501",
                    "pilotname": "Pilot, Pia", "comment": "Thermik", "accesstoken": "TOKEN"}
    db_session.expire_all()
    flight = db_session.get(Flight, flight.id)
    assert flight.finalized_at is not None and flight.vereinsflieger_external_id == "9000"
    assert "im Vereinsflieger" in client.get("/flugbuch").text


def test_pressing_twice_sends_once(client, db_session, linked, fake):
    green_flight(db_session, linked)
    login(client, "desk")
    close(client)
    close(client)
    client.post("/flugbuch/uebertragen", data={"day": str(today_local())})
    assert len(fake.sent()) == 1


def test_flight_typed_in_by_hand_is_linked_not_sent_again(client, db_session, linked, fake):
    flight = green_flight(db_session, linked)
    day = today_local().isoformat()
    fake.flights.append({"flid": "777", "callsign": "HB-1811", "departuretime": f"{day} 11:02",
                         "arrivaltime": f"{day} 11:40", "deleted": "0"})
    login(client, "desk")
    assert "1 war schon im Vereinsflieger" in close(client).text
    assert fake.sent() == []
    db_session.expire_all()
    assert db_session.get(Flight, flight.id).vereinsflieger_external_id == "777"


def test_tow_goes_with_the_glider_flight(client, db_session, linked, fake):
    tow = add_flight(db_session, linked["tow"], linked["bob"], "11:00", "11:08", verified_by_pilot=True,
                     flight_type="F", billing="none", launch_method=LaunchMethod.SELF)
    glider = green_flight(db_session, linked, launch_method=LaunchMethod.AEROTOW, tow_flight_id=tow.id,
                          tow_glider_id=linked["tow"].id)
    login(client, "desk")
    close(client)
    [sent] = fake.sent()
    assert sent["callsign"] == "HB-1811" and sent["starttype"] == 3
    assert (sent["towcallsign"], sent["towuidpilot"], sent["towpilotname"], sent["towtime"]) == \
        ("D-EDUY", "503", "Brunner, Bob", 8)
    db_session.expire_all()
    assert db_session.get(Flight, glider.id).vereinsflieger_external_id == "9000"


def test_tow_plane_flight_nobody_was_towed_by_is_not_green(client, db_session, linked, fake):
    add_flight(db_session, linked["tow"], linked["bob"], "15:00", "15:08", verified_by_pilot=True,
               flight_type="F", billing="none", launch_method=LaunchMethod.SELF)
    login(client, "desk")
    assert "Schlepp ohne geschleppten Flug" in client.get("/flugbuch").text
    assert close(client).status_code == 409


@pytest.mark.parametrize("change, problem", [
    (lambda f, w: setattr(f, "launch_method", None), "Startart fehlt"),
    (lambda f, w: setattr(f, "takeoff_airfield_icao", None), "Startort fehlt"),
    (lambda f, w: setattr(w["pia"], "vereinsflieger_member_id", None), "Pilot nicht mit Vereinsflieger verknüpft"),
    (lambda f, w: (setattr(f, "pilot_id", None), setattr(f, "pilot_name", "Gast Hans")),
     "Pilot zahlt, ist aber kein Mitglied"),
    (lambda f, w: (setattr(f, "launch_method", LaunchMethod.AEROTOW)), "Schleppflugzeug fehlt"),
])
def test_what_keeps_a_flight_from_being_green(client, db_session, linked, fake, change, problem):
    flight = green_flight(db_session, linked)
    change(flight, linked)
    db_session.commit()
    login(client, "desk")
    assert problem in client.get("/flugbuch").text
    assert close(client).status_code == 409


def test_guest_on_a_trial_flight_paid_by_another_member_is_green(client, db_session, linked, fake):
    green_flight(db_session, linked, flight_type="SF", billing="other_member", billing_member_id=linked["bob"].id)
    flight = db_session.query(Flight).one()
    flight.pilot_id, flight.pilot_name, flight.companion_id = None, "Gast Hans", linked["pia"].id
    db_session.commit()
    login(client, "desk")
    close(client)
    [sent] = fake.sent()
    assert (sent["pilotname"], sent["uidattendant"], sent["ftid"], sent["chargemode"], sent["uidcharge"]) == \
        ("Gast Hans", "501", 17, 7, "503")
    assert "uidpilot" not in sent


def test_refused_flight_can_be_sent_again(client, db_session, linked, fake):
    flight = green_flight(db_session, linked)
    fake.refuse = True
    login(client, "desk")
    page = close(client).text
    assert "Fehler: Flug übertragen fehlgeschlagen: Startart nicht erlaubt" in page
    assert "Übertragen wiederholen" in page
    db_session.expire_all()
    assert db_session.get(Flight, flight.id).vereinsflieger_external_id is None
    client.post("/flugbuch/uebertragen", data={"day": str(today_local())})
    db_session.expire_all()
    assert db_session.get(Flight, flight.id).vereinsflieger_external_id == "9000"
    assert len(fake.flights) == 1


def test_lost_answer_is_looked_up_not_sent_twice(client, db_session, linked, fake):
    flight = green_flight(db_session, linked)
    fake.lose_answer = True
    login(client, "desk")
    assert "antwortet nicht" in close(client).text
    db_session.expire_all()
    assert db_session.get(Flight, flight.id).vereinsflieger_external_id == push.PENDING
    client.post("/flugbuch/uebertragen", data={"day": str(today_local())})  # too soon: maybe still being sent
    assert len(fake.sent()) == 1
    flight = db_session.get(Flight, flight.id)
    flight.vereinsflieger_synced_at = datetime.now(timezone.utc) - push.PENDING_STALE - timedelta(seconds=1)
    db_session.commit()
    client.post("/flugbuch/uebertragen", data={"day": str(today_local())})
    db_session.expire_all()
    assert db_session.get(Flight, flight.id).vereinsflieger_external_id == "9000"
    assert len(fake.sent()) == 1 and len(fake.flights) == 1


def test_flight_corrected_after_reopening_is_corrected_there(client, db_session, linked, fake):
    flight = green_flight(db_session, linked)
    login(client, "admin")
    close(client)
    client.post(f"/admin/finalize/{today_local()}/unlock")
    db_session.expire_all()
    flight = db_session.get(Flight, flight.id)
    flight.landing_time = today_at("12:00")
    db_session.add(FlightAuditEntry(flight_id=flight.id, changed_by_pilot_id=linked["admin"].id,
                                    field_name="landing_time", old_value="11:45", new_value="12:00"))
    db_session.commit()
    assert "dort korrigiert" in close(client).text
    [edited] = fake.sent("flight/edit/9000")
    assert edited["arrivaltime"].endswith("12:00")
    assert len(fake.sent()) == 1


def test_without_vereinsflieger_closing_is_as_before(client, db_session, linked):
    add_flight(db_session, linked["glider"], linked["pia"], "11:00", "11:45")  # not even confirmed
    login(client, "desk")
    page = client.get("/flugbuch").text
    assert "übertragen" not in page
    close(client)
    db_session.expire_all()
    assert db_session.query(Flight).one().finalized_at is not None
