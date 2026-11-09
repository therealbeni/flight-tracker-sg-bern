"""Members, aircraft and places seen in the club's Vereinsflieger, and linking
pilots to their Vereinsflieger member (Verwaltung > Vereinsflieger)."""

from datetime import date

import pytest

import vereinsflieger as vf
import vereinsflieger_directory as directory
from models import FLIGHT_TYPES, VEREINSFLIEGER_FTID, VEREINSFLIEGER_STARTTYPE, LaunchMethod, Pilot, VereinsfliegerEntry
from test_flugbuch import login, world  # noqa: F401 - world is a fixture

# As flight/list/daterange gives them (all values strings), names made up.
VF_FLIGHTS = [
    {"flid": "1", "dateofflight": "2026-10-10", "callsign": "HB-1811", "uidpilot": "501", "pilotname": "Pilot, Pia",
     "uidattendant": "502", "attendantname": "Lehrer, Lukas", "departurelocation": "Bern-Belp Gld LSZB",
     "arrivallocation": "Bern-Belp Gld LSZB", "deleted": "0"},
    {"flid": "2", "dateofflight": "2026-10-12", "callsign": "HB-1811", "uidpilot": "501", "pilotname": "Pilot, Pia",
     "uidattendant": "0", "attendantname": "Gast Hans", "departurelocation": "Bern-Belp Gld LSZB",
     "arrivallocation": "Zweisimmen LSTZ", "deleted": "0"},
    {"flid": "3", "dateofflight": "2026-10-12", "callsign": "D-EDUY", "uidpilot": "503", "pilotname": "Brunner, Bob",
     "uidattendant": "0", "attendantname": "", "departurelocation": "Bern-Belp LSZB",
     "arrivallocation": "Bern-Belp LSZB", "deleted": "0"},
    {"flid": "4", "dateofflight": "2026-10-13", "callsign": "HB-9999", "uidpilot": "504", "pilotname": "Weg, Gelöscht",
     "departurelocation": "Thun LSZW", "arrivallocation": "Thun LSZW", "deleted": "1"},
]


class FakeVereinsflieger:
    def __init__(self):
        self.requests = []

    def __call__(self, method, url, fields):
        endpoint = url.removeprefix(vf.BASE_URL)
        self.requests.append(endpoint)
        if endpoint == "auth/accesstoken":
            return vf.Response(200, {"accesstoken": "TOKEN"})
        if endpoint == "flight/list/daterange":
            return vf.Response(200, {**{str(i): f for i, f in enumerate(VF_FLIGHTS)}, "httpstatuscode": 200})
        return vf.Response(200, {"httpstatuscode": 200})


@pytest.fixture
def readonly(monkeypatch):
    fake = FakeVereinsflieger()
    monkeypatch.setattr(vf, "http_transport", fake)
    for name, value in [("MODE", "readonly"), ("APPKEY", "KEY"), ("USER", "fdl"), ("PASSWORD", "geheim")]:
        monkeypatch.setenv(f"VEREINSFLIEGER_{name}", value)
    return fake


def load(db):
    db.add_all(directory.collect(VF_FLIGHTS))
    db.commit()


def test_collect_members_aircraft_and_places_without_guests_or_deleted_flights():
    entries = {(e.kind, e.key): e for e in directory.collect(VF_FLIGHTS)}
    members = {k: e.name for (kind, k), e in entries.items() if kind == directory.MEMBER}
    assert members == {"501": "Pilot, Pia", "502": "Lehrer, Lukas", "503": "Brunner, Bob"}
    pia = entries[(directory.MEMBER, "501")]
    assert (pia.flights, pia.last_flight) == (2, date(2026, 10, 12))
    assert {k for kind, k in entries if kind == directory.AIRCRAFT} == {"HB-1811", "D-EDUY"}
    assert entries[(directory.PLACE, "Bern-Belp Gld LSZB")].flights == 2  # counted once per flight


def test_place_names_take_the_name_the_club_uses_most(db_session):
    load(db_session)
    assert directory.place_names(db_session) == {"LSZB": "Bern-Belp Gld LSZB", "LSTZ": "Zweisimmen LSTZ"}


@pytest.mark.parametrize("a, b, same", [
    ("Bürgi, Benjamin", "Benjamin Bürgi", True),
    ("Bürgi, Benjamin", "benjamin burgi", True),
    ("Flury Delia", "Delia Flury", True),
    ("Bürgi, Benjamin", "Benjamin Bürgi-Muster", False),
])
def test_name_key(a, b, same):
    assert (directory.name_key(a) == directory.name_key(b)) is same


def test_every_flugart_and_startart_has_its_vereinsflieger_number():
    assert set(VEREINSFLIEGER_FTID) == set(FLIGHT_TYPES)
    assert len(set(VEREINSFLIEGER_FTID.values())) == len(FLIGHT_TYPES)
    assert set(VEREINSFLIEGER_STARTTYPE) == set(LaunchMethod)


def test_admin_loads_from_vereinsflieger_reading_only(client, db_session, world, readonly):
    login(client, "admin")
    resp = client.post("/admin/vereinsflieger/reload", follow_redirects=True)
    assert "4 Flüge aus dem Vereinsflieger gelesen" in resp.text
    assert readonly.requests == ["auth/accesstoken", "auth/signin", "flight/list/daterange",
                                 "auth/signout/TOKEN"]
    assert db_session.query(VereinsfliegerEntry).count() == 3 + 2 + 3
    # A second load replaces the first, it doesn't add to it.
    client.post("/admin/vereinsflieger/reload")
    assert db_session.query(VereinsfliegerEntry).count() == 8


def test_load_is_refused_while_the_connection_is_off(client, db_session, world):
    login(client, "admin")
    assert "Aus dem Vereinsflieger laden" not in client.get("/admin/vereinsflieger").text
    resp = client.post("/admin/vereinsflieger/reload")
    assert resp.status_code == 400 and "ausgeschaltet" in resp.text


def test_page_suggests_the_member_with_the_same_name_but_does_not_link(client, db_session, world):
    load(db_session)
    login(client, "admin")
    page = client.get("/admin/vereinsflieger").text
    assert "Vorschlag" in page
    assert '<option value="501" selected' in page  # Pia Pilot = "Pilot, Pia"
    db_session.expire_all()
    assert db_session.get(Pilot, world["pia"].id).vereinsflieger_member_id is None


def test_admin_links_and_unlinks_a_pilot(client, db_session, world):
    load(db_session)
    login(client, "admin")
    client.post(f"/admin/vereinsflieger/pilots/{world['bob'].id}", data={"uid": "503"})
    db_session.expire_all()
    assert db_session.get(Pilot, world["bob"].id).vereinsflieger_member_id == "503"
    assert "verknüpft" in client.get("/admin/vereinsflieger").text
    client.post(f"/admin/vereinsflieger/pilots/{world['bob'].id}", data={"uid": ""})
    db_session.expire_all()
    assert db_session.get(Pilot, world["bob"].id).vereinsflieger_member_id is None


@pytest.mark.parametrize("uid, message", [("999", "kennt der Tracker"), ("503", "schon mit Bob Brunner")])
def test_link_refuses_unknown_members_and_members_already_linked(client, db_session, world, uid, message):
    load(db_session)
    world["bob"].vereinsflieger_member_id = "503"
    db_session.commit()
    login(client, "admin")
    resp = client.post(f"/admin/vereinsflieger/pilots/{world['pia'].id}", data={"uid": uid})
    assert resp.status_code == 400 and message in resp.text
    db_session.expire_all()
    assert db_session.get(Pilot, world["pia"].id).vereinsflieger_member_id is None


@pytest.mark.parametrize("who", ["pia", "desk"])
def test_only_admins_see_or_change_the_mapping(client, db_session, world, who):
    load(db_session)
    login(client, who)
    assert client.get("/admin/vereinsflieger", follow_redirects=False).status_code in (303, 403)
    client.post(f"/admin/vereinsflieger/pilots/{world['pia'].id}", data={"uid": "501"})
    db_session.expire_all()
    assert db_session.get(Pilot, world["pia"].id).vereinsflieger_member_id is None
