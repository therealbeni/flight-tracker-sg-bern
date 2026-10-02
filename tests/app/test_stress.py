"""Things going wrong at the same time: two pilots and one aircraft, two
people editing one flight, the tracker writing while someone types, double
taps, aircraft and accounts disabled mid-day, overlong input."""

from datetime import datetime, timezone

from models import Flight, FlightSource, GliderClaim, PilotStatus
from test_flugbuch import add_flight, login, new_flight_form, world  # noqa: F401 - world is a fixture
from timeutil import today_local


def claim(client, glider, mode="next", **extra):
    return client.post(f"/claim/{glider.claim_token}", data={"mode": mode, **extra})


def active(db, **filters):
    db.expire_all()
    return db.query(GliderClaim).filter(GliderClaim.active_at(datetime.now(timezone.utc))).filter_by(**filters).all()


# ---- two pilots, one aircraft

def test_taking_over_someone_elses_check_in_needs_a_confirmation(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    login(client, "bob")
    page = claim(client, world["glider"])
    assert page.status_code == 409
    assert "Pia Pilot ist schon eingecheckt" in page.text and 'name="takeover"' in page.text
    assert [c.pilot_id for c in active(db_session, glider_id=world["glider"].id)] == [world["pia"].id]

    claim(client, world["glider"], takeover="1")
    assert [c.pilot_id for c in active(db_session, glider_id=world["glider"].id)] == [world["bob"].id]


def test_the_pilot_whose_check_in_was_taken_over_is_told(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    login(client, "bob")
    claim(client, world["glider"], takeover="1")
    login(client, "pia")
    assert "Bob Brunner hat HB-1811 übernommen" in client.get("/dashboard").text
    claim(client, world["glider"], takeover="1")  # Pia takes it back: no more notice
    assert "übernommen" not in client.get("/dashboard").text


def test_claim_page_warns_before_the_pilot_even_tries(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    login(client, "bob")
    page = client.get(f"/claim/{world['glider'].claim_token}").text
    assert "Pia Pilot ist schon eingecheckt" in page and 'name="takeover"' in page


def test_checking_in_on_a_flying_aircraft_is_for_its_next_start(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end=None)
    login(client, "bob")
    page = client.get(f"/claim/{world['glider'].claim_token}").text
    assert "Gerade in der Luft" in page
    assert claim(client, world["glider"]).status_code == 200  # no one to take over from


def test_one_pilot_one_aircraft_at_a_time(client, db_session, world):
    from models import Glider

    other = Glider(registration="HB-3131", ogn_device_id="4B50E2")
    db_session.add(other)
    db_session.commit()
    login(client, "pia")
    claim(client, world["glider"])
    page = claim(client, other)
    assert "Dein Check-in auf HB-1811 ist beendet" in page.text
    assert [c.glider_id for c in active(db_session, pilot_id=world["pia"].id)] == [other.id]


def test_a_whole_day_check_in_stays_when_the_tow_pilot_flies_a_glider(client, db_session, world):
    login(client, "bob")
    claim(client, world["tow"], mode="day")
    claim(client, world["glider"])
    assert {c.glider_id for c in active(db_session, pilot_id=world["bob"].id)} == {world["tow"].id, world["glider"].id}


def test_check_in_while_logged_out_returns_to_the_aircraft_after_login(client, db_session, world):
    client.post("/logout")
    url = f"/claim/{world['glider'].claim_token}"
    resp = client.get(url, follow_redirects=False)
    assert resp.headers["location"] == f"/login?next={url}"
    assert f'name="next" value="{url}"' in client.get(resp.headers["location"]).text
    resp = client.post("/login", data={"email": "pia@example.com", "password": "password123", "next": url},
                       follow_redirects=False)
    assert resp.headers["location"] == url


def test_login_never_redirects_off_site(client, db_session, world):
    resp = client.post("/login", data={"email": "pia@example.com", "password": "password123",
                                       "next": "https://evil.example/claim"}, follow_redirects=False)
    assert resp.headers["location"] == "/dashboard"


# ---- aircraft and accounts disabled during the day

def test_deactivating_an_aircraft_ends_its_check_ins(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    login(client, "admin")
    client.post(f"/admin/gliders/{world['glider'].id}/toggle-active")
    assert active(db_session, glider_id=world["glider"].id) == []
    login(client, "pia")
    assert claim(client, world["glider"]).status_code == 404  # and no new ones


def test_disabling_an_account_ends_its_check_ins(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    login(client, "admin")
    client.post(f"/admin/pilots/{world['pia'].id}/reject")
    db_session.refresh(world["pia"])
    assert world["pia"].status is PilotStatus.REJECTED
    assert active(db_session, pilot_id=world["pia"].id) == []


# ---- two people (or the tracker) changing one flight

def edit_form(flight, **overrides):
    """What the Flugbuch form submits for `flight` as it was loaded."""
    from flight_form import version_of

    data = new_flight_form(flight.glider, pilot_id=str(flight.pilot_id or ""), takeoff_time="11:00",
                           landing_time="11:45" if flight.landing_time else "", version=version_of(flight))
    data.update(overrides)
    return data


def test_a_flight_changed_meanwhile_is_not_overwritten(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    loaded = edit_form(f)  # the FDL opens the flight...
    login(client, "pia")  # ...meanwhile Pia corrects the landing on her phone
    client.post(f"/flights/{f.id}", data=edit_form(f, landing_time="12:10"))
    login(client, "desk")
    resp = client.post(f"/flights/{f.id}", data={**loaded, "notes": "Gast"})  # the FDL saves the old state
    assert resp.status_code == 409 and "inzwischen geändert" in resp.text
    db_session.refresh(f)
    assert f.notes is None and f.landing_time.astimezone().strftime("%H:%M") != ""
    assert "12:10" in resp.text  # the form now shows the current values


def test_the_tracker_landing_a_flight_while_someone_edits_it_is_kept(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"], end=None)  # in the air
    loaded = edit_form(f)  # Pia opens it while flying (landing empty)
    f.landing_time = datetime.now(timezone.utc)  # the tracker records the landing
    db_session.commit()
    login(client, "pia")
    resp = client.post(f"/flights/{f.id}", data={**loaded, "notes": "Thermik"})
    assert resp.status_code == 409
    db_session.refresh(f)
    assert f.landing_time is not None


def test_saving_without_a_concurrent_change_still_works(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "pia")
    assert client.post(f"/flights/{f.id}", data=edit_form(f, notes="ok"), follow_redirects=False).status_code == 303


# ---- duplicates

def test_manual_and_tracked_flight_of_the_same_aircraft_and_time_are_flagged(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="14:00", end="14:40")
    login(client, "desk")
    client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_id=str(world["pia"].id),
                                                  takeoff_time="14:02", landing_time="14:41"))
    assert db_session.query(Flight).filter_by(source=FlightSource.MANUAL).count() == 1
    assert client.get("/flugbuch").text.count("doppelt?") == 2
    assert "doppelt?" in client.get("/dashboard").text  # FDL: Zu prüfen


# ---- overlong input

def test_overlong_names_get_a_message_not_a_crash(client, db_session, world):
    login(client, "desk")
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_name="X" * 300))
    assert resp.status_code == 400 and "zu lang" in resp.text


def test_overlong_aircraft_and_airfield_entries_get_a_message(client, db_session, world):
    login(client, "admin")
    resp = client.post("/admin/gliders", data={"registration": "H" * 40, "kind": "glider"})
    assert resp.status_code == 400 and "zu lang" in resp.text
    resp = client.post("/admin/airfields", data={"icao": "A" * 20, "name": "x", "latitude": "46", "longitude": "7"})
    assert resp.status_code == 400 and "zu lang" in resp.text


def test_adding_an_aircraft_twice_gets_a_message(client, db_session, world):
    login(client, "admin")
    resp = client.post("/admin/gliders", data={"registration": "hb-1811", "kind": "glider"})
    assert resp.status_code == 400 and "gibt es schon" in resp.text


def test_signing_up_twice_at_once_gets_a_message(client, db_session, world):
    # The second request of a double tap: the account exists by the time it's saved.
    resp = client.post("/signup", data={"full_name": "Pia Pilot", "email": "PIA@example.com", "password": "x" * 10})
    assert resp.status_code == 400 and "schon ein Konto" in resp.text


# ---- truly at the same time (needs Postgres: SQLite has no row locks)

def test_two_pilots_checking_in_at_the_same_moment_do_not_both_get_it(db_session, world):
    import threading

    import pytest
    from fastapi.testclient import TestClient

    import main as main_module
    from database import engine

    if engine.dialect.name != "postgresql":
        pytest.skip("row locks need Postgres (dev/test.sh)")
    for round_ in range(5):
        clients = {}
        for who in ("pia", "bob"):
            clients[who] = TestClient(main_module.app)
            login(clients[who], who)
        start = threading.Barrier(2)
        results = {}

        url = f"/claim/{world['glider'].claim_token}"  # read here: the test's session isn't thread-safe

        def check_in(who):
            start.wait()
            results[who] = clients[who].post(url, data={"mode": "next"}).status_code

        threads = [threading.Thread(target=check_in, args=(who,)) for who in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results.values()) == [200, 409], f"round {round_}: {results}"
        assert len(active(db_session, glider_id=world["glider"].id)) == 1
        for c in active(db_session, glider_id=world["glider"].id):  # free again for the next round
            c.cancelled_at = datetime.now(timezone.utc)
        db_session.commit()
