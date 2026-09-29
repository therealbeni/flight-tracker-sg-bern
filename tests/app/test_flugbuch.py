"""Flugbuch (club PC day view), adding/correcting/deleting flights, checkout."""

from datetime import datetime, timedelta, timezone

import pytest

from models import (Airfield, Flight, FlightAuditEntry, FlightSource, Glider, GliderClaim, Pilot, PilotRole,
                    PilotStatus)
from security import hash_password
from timeutil import combine_local, today_local

PASSWORD = "password123"


@pytest.fixture
def world(db_session):
    """Two pilots, the club PC account, an admin, a glider and a tow plane."""
    people = {}
    for key, name, role in [("admin", "Anna Admin", PilotRole.ADMIN), ("pia", "Pia Pilot", PilotRole.PILOT),
                            ("bob", "Bob Brunner", PilotRole.PILOT), ("desk", "Startstelle LSZB", PilotRole.FLIGHTDESK)]:
        people[key] = Pilot(full_name=name, email=f"{key}@example.com", password_hash=hash_password(PASSWORD),
                            role=role, status=PilotStatus.APPROVED)
    db_session.add_all(people.values())
    db_session.add_all([Airfield(icao="LSZB", name="Bern", latitude=46.9, longitude=7.5, elevation_m=510),
                        Airfield(icao="LSTZ", name="Zweisimmen", latitude=46.5, longitude=7.4, elevation_m=935)])
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    return {**people, "glider": glider}


def login(client, who):
    client.post("/logout")
    client.post("/login", data={"email": f"{who}@example.com", "password": PASSWORD})


def today_at(hhmm):
    return combine_local(today_local(), hhmm)


def add_flight(db, glider, pilot=None, start="11:00", end="11:45", **kwargs):
    f = Flight(record_id=f"r-{start}-{getattr(pilot, 'id', 0)}", glider_id=glider.id,
               pilot_id=pilot.id if pilot else None, takeoff_time=today_at(start),
               landing_time=today_at(end) if end else None, takeoff_airfield_icao="LSZB",
               landing_airfield_icao="LSZB" if end else None, source=FlightSource.AUTO, **kwargs)
    if end:
        f.duration_min = (f.landing_time - f.takeoff_time).total_seconds() / 60
    db.add(f)
    db.commit()
    return f


def new_flight_form(glider, **overrides):
    data = {"day": str(today_local()), "glider_id": str(glider.id), "pilot_id": "", "pilot_name": "",
            "companion_id": "", "companion_name": "", "launch_method": "F", "takeoff_airfield_icao": "LSZB",
            "takeoff_time": "14:00", "landing_airfield_icao": "LSZB", "landing_time": "14:35", "landings": "1",
            "notes": ""}
    data.update(overrides)
    return data


def test_flugbuch_lists_the_days_flights_in_order(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], "13:00", "13:30")
    add_flight(db_session, world["glider"], world["bob"], "10:00", "10:50")
    add_flight(db_session, world["glider"], None, "15:00", None)
    login(client, "pia")
    page = client.get("/flugbuch").text
    assert page.index("Bob Brunner") < page.index("Pia Pilot")
    assert "3 Flüge" in page
    assert "Pilot fehlt" in page or "fehlt" in page
    assert "in der Luft" in page


def test_club_pc_adds_a_guest_flight(client, db_session, world):
    login(client, "desk")
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_name="Gast Hans Muster",
                                                         companion_id=str(world["bob"].id)), follow_redirects=False)
    assert resp.status_code == 303
    f = db_session.query(Flight).one()
    assert f.source is FlightSource.MANUAL
    assert f.pilot_name == "Gast Hans Muster" and f.companion_id == world["bob"].id
    assert f.duration_min == pytest.approx(35)
    assert f.takeoff_time.replace(tzinfo=timezone.utc) == today_at("14:00")
    assert db_session.query(FlightAuditEntry).filter_by(flight_id=f.id, field_name="created").count() == 1
    assert "Gast Hans Muster" in client.get("/flugbuch").text


def test_pilot_can_add_own_flight_but_not_someone_elses(client, db_session, world):
    login(client, "pia")
    ok = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_id=str(world["pia"].id)),
                     follow_redirects=False)
    assert ok.status_code == 303
    nope = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_id=str(world["bob"].id),
                                                         takeoff_time="16:00", landing_time="16:30"))
    assert nope.status_code == 400
    assert "Pilot oder Begleiter" in nope.text
    assert db_session.query(Flight).count() == 1


def test_instructor_logs_a_guest_flight_as_companion(client, db_session, world):
    login(client, "pia")
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_name="Schnupperflug Eva",
                                                         companion_id=str(world["pia"].id)), follow_redirects=False)
    assert resp.status_code == 303


@pytest.mark.parametrize("overrides, message", [
    ({"landing_time": "13:50"}, "Die Landung muss nach dem Start sein."),
    ({"takeoff_time": "25:00"}, "Stunden:Minuten"),
    ({"takeoff_time": ""}, "Bitte eine Startzeit eingeben."),
    ({"takeoff_airfield_icao": "XXXX"}, "«XXXX» kennen wir noch nicht"),
    ({"glider_id": ""}, "Bitte ein Flugzeug wählen."),
    ({"glider_id": "abc"}, "Bitte ein Flugzeug wählen."),
    ({"landings": "viele"}, "Anzahl Landungen"),
    ({"launch_method": "Z"}, "Unbekannte Startart."),
    ({"pilot_id": "99999"}, "Diesen Piloten gibt es nicht."),
])
def test_bad_input_gives_a_message_not_an_error(client, db_session, world, overrides, message):
    login(client, "desk")
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], **overrides))
    assert resp.status_code == 400
    assert message in resp.text
    assert db_session.query(Flight).count() == 0
    assert 'value="' + overrides.get("notes", "") in resp.text  # form keeps what was typed


def test_same_person_cannot_be_pilot_and_companion(client, db_session, world):
    login(client, "desk")
    pid = str(world["pia"].id)
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_id=pid, companion_id=pid))
    assert "nicht dieselbe Person" in resp.text


def test_correcting_times_recomputes_duration_and_clears_estimate(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"], "11:00", "11:45", landing_estimated=True)
    login(client, "pia")
    from test_flights import form_for
    client.post(f"/flights/{f.id}", data=form_for(f, landing_time="12:10"))
    db_session.refresh(f)
    assert f.duration_min == pytest.approx(70)
    assert not f.landing_estimated
    history = client.get(f"/flights/{f.id}").text
    assert "Landezeit" in history and "11:45" in history and "12:10" in history


def test_saving_without_changes_keeps_the_trackers_seconds(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"], "11:00", "11:45")
    f.takeoff_time += timedelta(seconds=17)
    db_session.commit()
    login(client, "pia")
    from test_flights import form_for
    client.post(f"/flights/{f.id}", data=form_for(f))
    db_session.refresh(f)
    assert f.takeoff_time.second == 17


def test_club_pc_edits_anyones_flight_and_edit_error_returns_to_flugbuch(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "desk")
    from test_flights import form_for
    ok = client.post(f"/flights/{f.id}", data=form_for(f, landings="3", next="/flugbuch"), follow_redirects=False)
    assert ok.headers["location"] == "/flugbuch"
    db_session.refresh(f)
    assert f.landings == 3
    bad = client.post(f"/flights/{f.id}", data=form_for(f, landing_time="09:00", next="/flugbuch"))
    assert bad.status_code == 400 and "Flug bearbeiten" in bad.text


def test_next_never_leaves_the_site(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "desk")
    from test_flights import form_for
    for evil in ["https://evil.example/", "//evil.example/", "/\\evil.example"]:
        resp = client.post(f"/flights/{f.id}", data=form_for(f, next=evil), follow_redirects=False)
        assert resp.headers["location"] == f"/flights/{f.id}"


def test_deleting_is_for_club_pc_and_admins_and_hides_the_flight(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "pia")
    assert client.post(f"/flights/{f.id}/delete").status_code == 403
    login(client, "desk")
    assert client.post(f"/flights/{f.id}/delete", follow_redirects=False).status_code == 303
    db_session.refresh(f)
    assert f.deleted_at is not None
    assert client.get(f"/flights/{f.id}").status_code == 404
    login(client, "pia")
    assert "HB-1811" not in client.get("/flugbuch").text.split("<tbody>")[1]
    assert "Noch keine Flüge" in client.get("/logbook").text


def test_finalized_day_cannot_be_edited(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"], finalized_at=datetime.now(timezone.utc))
    login(client, "desk")
    page = client.get("/flugbuch").text
    assert "abgeschlossen" in page and "Flug hinzufügen</button>" not in page
    from test_flights import form_for
    assert client.post(f"/flights/{f.id}", data=form_for(f, landings="2")).status_code == 403


def test_checkout_confirms_flights_and_ends_check_ins(client, db_session, world):
    landed = add_flight(db_session, world["glider"], world["pia"], "10:00", "10:40")
    flying = add_flight(db_session, world["glider"], world["pia"], "12:00", None)
    as_companion = add_flight(db_session, world["glider"], world["bob"], "13:00", "13:20", companion_id=world["pia"].id)
    now = datetime.now(timezone.utc)
    claim = GliderClaim(glider_id=world["glider"].id, pilot_id=world["pia"].id, claimed_at=now,
                        expires_at=now + timedelta(hours=5), whole_day=True)
    db_session.add(claim)
    db_session.commit()

    login(client, "pia")
    page = client.get(f"/auschecken/{world['pia'].id}").text
    assert "als Begleiter von Bob Brunner" in page
    assert "fehlt noch etwas" in page  # the flight still in the air
    client.post(f"/auschecken/{world['pia'].id}", data={"day": str(today_local())})
    for f in (landed, flying, as_companion, claim):
        db_session.refresh(f)
    assert landed.verified_by_pilot and as_companion.verified_by_pilot
    assert not flying.verified_by_pilot
    assert claim.cancelled_at is not None


def test_only_yourself_or_the_club_pc_can_check_out(client, db_session, world):
    login(client, "pia")
    assert client.get(f"/auschecken/{world['bob'].id}").status_code == 403
    assert client.post(f"/auschecken/{world['bob'].id}", data={"day": str(today_local())}).status_code == 403
    login(client, "desk")
    assert client.get(f"/auschecken/{world['bob'].id}").status_code == 200


def test_club_pc_lands_on_the_flugbuch_and_is_not_an_admin(client, db_session, world):
    login(client, "desk")
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/flugbuch"
    assert client.get("/admin/pilots").status_code == 403


def test_admin_sets_roles_but_not_their_own(client, db_session, world):
    login(client, "admin")
    client.post(f"/admin/pilots/{world['bob'].id}/role", data={"role": "flightdesk"})
    client.post(f"/admin/pilots/{world['admin'].id}/role", data={"role": "pilot"})
    db_session.refresh(world["bob"])
    db_session.refresh(world["admin"])
    assert world["bob"].role is PilotRole.FLIGHTDESK
    assert world["admin"].role is PilotRole.ADMIN


def test_club_pc_account_is_not_offered_as_pilot(client, db_session, world):
    login(client, "desk")
    assert "Startstelle LSZB</option>" not in client.get("/flugbuch").text
