"""Flugbuch (club PC day view), adding/correcting/deleting flights, checkout."""

from datetime import datetime, timedelta, timezone

import pytest

from models import (AircraftKind, Airfield, Flight, FlightAuditEntry, FlightSource, Glider, GliderClaim,
                    LaunchMethod, Pilot, PilotRole, PilotStatus)
from security import hash_password
from timeutil import combine_local, fmt_date, today_local

PASSWORD = "password123"


@pytest.fixture
def world(db_session):
    """Two pilots, the club PC account, an admin, a glider and a tow plane."""
    people = {}
    for key, name, role in [("admin", "Anna Admin", PilotRole.ADMIN), ("pia", "Pia Pilot", PilotRole.PILOT),
                            ("bob", "Bob Brunner", PilotRole.PILOT), ("desk", "Flugdienstleiter LSZB", PilotRole.FDL)]:
        people[key] = Pilot(full_name=name, email=f"{key}@example.com", password_hash=hash_password(PASSWORD),
                            role=role, status=PilotStatus.APPROVED)
    db_session.add_all(people.values())
    db_session.add_all([Airfield(icao="LSZB", name="Bern", latitude=46.9, longitude=7.5, elevation_m=510),
                        Airfield(icao="LSTZ", name="Zweisimmen", latitude=46.5, longitude=7.4, elevation_m=935)])
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    tow = Glider(registration="D-EDUY", ogn_device_id="3D0EB4", kind=AircraftKind.TOWPLANE)
    db_session.add_all([glider, tow])
    db_session.commit()
    return {**people, "glider": glider, "tow": tow}


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
    """A self-launched flight today, as "Flug hinzufügen" submits it."""
    data = {"day": str(today_local()), "glider_id": str(glider.id), "launch_method": "E", "flight_type": "N",
            "pilot_id": "", "pilot_name": "", "companion_id": "", "companion_name": "",
            "flight_date": fmt_date(today_local()), "takeoff_time": "14:00", "landing_time": "14:35",
            "takeoff_airfield_icao": "LSZB", "landing_airfield_icao": "LSZB", "billing": "pilot",
            "billing_member_id": "", "tow_glider_id": "", "tow_pilot_id": "", "notes": ""}
    data.update(overrides)
    return data


def test_flugbuch_lists_the_days_flights_in_order(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], "13:00", "13:30")
    add_flight(db_session, world["glider"], world["bob"], "10:00", "10:50")
    add_flight(db_session, world["glider"], None, "15:00", None)
    login(client, "pia")
    page = client.get("/flugbuch").text
    assert page.index("Bob Brunner") < page.index("Pia Pilot")
    assert "Anz. Flüge: 3" in page
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
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_name="Eva Schnupper",
                                                         companion_id=str(world["pia"].id), flight_type="SF",
                                                         billing="guest"), follow_redirects=False)
    assert resp.status_code == 303
    f = db_session.query(Flight).one()
    assert (f.pilot_name, f.companion_id, f.flight_type, f.billing) == ("Eva Schnupper", world["pia"].id, "SF", "guest")


@pytest.mark.parametrize("overrides, message", [
    ({"landing_time": "13:50"}, "Die Landung muss nach dem Start sein."),
    ({"takeoff_time": "25:00"}, "Stunden:Minuten"),
    ({"takeoff_time": ""}, "Bitte eine Startzeit eingeben."),
    ({"takeoff_airfield_icao": "XXXX"}, "«XXXX» kennen wir noch nicht"),
    ({"glider_id": ""}, "Bitte ein Flugzeug wählen."),
    ({"glider_id": "abc"}, "Bitte ein Flugzeug wählen."),
    ({"flight_date": "31.02.2026"}, "Tag.Monat.Jahr"),
    ({"flight_date": "gestern"}, "Tag.Monat.Jahr"),
    ({"launch_method": "Z"}, "Unbekannte Startart."),
    ({"flight_type": "X"}, "Unbekannte Flugart."),
    ({"billing": "gratis"}, "Unbekannte Abrechnungsart."),
    ({"billing": "other_member"}, "welches Mitglied bezahlt"),
    ({"pilot_id": "99999"}, "Pilot: diese Person gibt es nicht."),
    ({"pilot_id": "abc"}, "Pilot: diese Person gibt es nicht."),
    ({"companion_id": "gast"}, "Namen des Gasts"),
    ({"launch_method": "F"}, "Bitte das Schleppflugzeug wählen."),
    ({"launch_method": "F", "tow_glider_id": "TOW", "tow_pilot_id": "99999"}, "Schlepppilot: diese Person"),
])
def test_bad_input_gives_a_message_not_an_error(client, db_session, world, overrides, message):
    login(client, "desk")
    overrides = {k: str(world["tow"].id) if v == "TOW" else v for k, v in overrides.items()}
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
    assert "Landung" in history and "11:45" in history and "12:10" in history


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
    ok = client.post(f"/flights/{f.id}", data=form_for(f, flight_type="S", next="/flugbuch"), follow_redirects=False)
    assert ok.headers["location"] == "/flugbuch"
    db_session.refresh(f)
    assert f.flight_type == "S"
    bad = client.post(f"/flights/{f.id}", data=form_for(f, landing_time="09:00", next="/flugbuch"))
    assert bad.status_code == 400 and "Flug bearbeiten" in bad.text


def test_next_never_leaves_the_site(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "desk")
    from test_flights import form_for
    for evil in ["https://evil.example/", "//evil.example/", "/\\evil.example"]:
        db_session.refresh(f)  # the form as loaded now (each save changes the flight)
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
    assert client.post(f"/flights/{f.id}", data=form_for(f, notes="nachträglich")).status_code == 403


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


def test_fdl_is_not_an_admin(client, db_session, world):
    login(client, "desk")
    assert client.get("/admin/pilots").status_code == 403


def test_admin_sets_roles_but_not_their_own(client, db_session, world):
    login(client, "admin")
    client.post(f"/admin/pilots/{world['bob'].id}/role", data={"role": "fdl"})
    client.post(f"/admin/pilots/{world['admin'].id}/role", data={"role": "pilot"})
    db_session.refresh(world["bob"])
    db_session.refresh(world["admin"])
    assert world["bob"].role is PilotRole.FDL
    assert world["admin"].role is PilotRole.ADMIN


def test_club_pc_account_is_not_offered_as_pilot(client, db_session, world):
    login(client, "desk")
    assert "Flugdienstleiter LSZB</option>" not in client.get("/flugbuch").text


# ---------------------------------------------------- Vereinsflieger fields


def test_times_can_be_typed_without_colon(client, db_session, world):
    # Phone number pads have no ":" - "1405", "14.05" and "14,05" all work.
    login(client, "desk")
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], takeoff_time="1405", landing_time="14,50"),
                       follow_redirects=False)
    assert resp.status_code == 303
    f = db_session.query(Flight).one()
    assert f.takeoff_time.replace(tzinfo=timezone.utc) == today_at("14:05")
    assert f.duration_min == pytest.approx(45)


def test_flight_date_can_be_corrected(client, db_session, world):
    yesterday = today_local() - timedelta(days=1)
    f = add_flight(db_session, world["glider"], world["pia"], "11:00", "11:45")
    login(client, "desk")
    from test_flights import form_for
    client.post(f"/flights/{f.id}", data=form_for(f, flight_date=yesterday.strftime("%d.%m.%y")))
    db_session.refresh(f)
    assert f.takeoff_time.replace(tzinfo=timezone.utc) == combine_local(yesterday, "11:00")
    assert f.landing_time.replace(tzinfo=timezone.utc) == combine_local(yesterday, "11:45")
    assert f.duration_min == pytest.approx(45)
    # Adding a flight for another day shows that day afterwards.
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], flight_date=fmt_date(yesterday)),
                       follow_redirects=False)
    assert resp.headers["location"] == f"/flugbuch?datum={yesterday}"


def test_guest_companion_and_unknown_pilot(client, db_session, world):
    login(client, "desk")
    client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_name="Hans Gast", companion_id="gast",
                                                  companion_name="Eva Gast"))
    f = db_session.query(Flight).one()
    assert (f.pilot_id, f.pilot_name, f.companion_id, f.companion_name) == (None, "Hans Gast", None, "Eva Gast")
    # "Keiner" as Begleiter drops a name typed before.
    from test_flights import form_for
    client.post(f"/flights/{f.id}", data=form_for(f, companion_id=""))
    db_session.refresh(f)
    assert f.companion_name is None
    # Picking a member as pilot drops the typed name.
    client.post(f"/flights/{f.id}", data=form_for(f, pilot_id=str(world["bob"].id)))
    db_session.refresh(f)
    assert (f.pilot_id, f.pilot_name) == (world["bob"].id, None)


def test_another_member_pays(client, db_session, world):
    login(client, "desk")
    client.post("/flugbuch", data=new_flight_form(world["glider"], pilot_id=str(world["pia"].id),
                                                  billing="other_member", billing_member_id=str(world["bob"].id)))
    f = db_session.query(Flight).one()
    assert (f.billing, f.billing_member_id) == ("other_member", world["bob"].id)
    assert "Anderes Mitglied (Bob Brunner)" in client.get(f"/flights/{f.id}").text


def test_aerotow_form_defaults_to_the_tow_plane_and_its_checked_in_pilot(client, db_session, world):
    # The tow pilot of the day checked in on D-EDUY earlier.
    now = datetime.now(timezone.utc)
    db_session.add(GliderClaim(glider_id=world["tow"].id, pilot_id=world["bob"].id, whole_day=True,
                               claimed_at=now - timedelta(hours=1), expires_at=now + timedelta(hours=1)))
    db_session.commit()
    login(client, "desk")
    page = client.get("/flugbuch").text
    tow_section = page[page.index('name="tow_glider_id"'):page.index("</fieldset>")]
    assert f'<option value="{world["tow"].id}" data-pilot="{world["bob"].id}" selected>D-EDUY' in tow_section
    assert f'<option value="{world["bob"].id}" selected>Bob Brunner' in tow_section

    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], launch_method="F",
                                                         tow_glider_id=str(world["tow"].id),
                                                         tow_pilot_id=str(world["bob"].id)), follow_redirects=False)
    assert resp.status_code == 303
    f = db_session.query(Flight).filter_by(glider_id=world["glider"].id).one()
    assert (f.tow_glider_id, f.tow_pilot_id) == (world["tow"].id, world["bob"].id)
    assert "D-EDUY, Bob Brunner" in client.get("/flugbuch").text


def test_tow_data_is_only_kept_for_aerotows(client, db_session, world):
    login(client, "desk")
    client.post("/flugbuch", data=new_flight_form(world["glider"], launch_method="W",
                                                  tow_glider_id=str(world["tow"].id),
                                                  tow_pilot_id=str(world["bob"].id)))
    f = db_session.query(Flight).one()
    assert f.launch_method is LaunchMethod.WINCH
    assert f.tow_glider_id is None and f.tow_pilot_id is None


def tracked_aerotow(db, world, tow_pilot=None):
    """A glider flight the tracker linked to the tow plane's own flight."""
    tow = add_flight(db, world["tow"], tow_pilot, "10:00", "10:08", launch_method=LaunchMethod.SELF,
                     flight_type="F", billing="none")
    glider = add_flight(db, world["glider"], world["pia"], "10:00", "11:00", launch_method=LaunchMethod.AEROTOW,
                        tow_flight_id=tow.id, tow_glider_id=world["tow"].id)
    return glider, tow


def test_tow_pilot_entered_on_the_glider_flight_also_goes_to_the_tow_flight(client, db_session, world):
    glider, tow = tracked_aerotow(db_session, world)
    login(client, "desk")
    from test_flights import form_for
    form = form_for(glider)
    assert form["tow_glider_id"] == str(world["tow"].id) and form["tow_pilot_id"] == ""
    client.post(f"/flights/{glider.id}", data={**form, "tow_pilot_id": str(world["bob"].id)})
    db_session.refresh(tow)
    assert tow.pilot_id == world["bob"].id


def test_switching_away_from_aerotow_unlinks_the_tow_flight(client, db_session, world):
    glider, tow = tracked_aerotow(db_session, world, tow_pilot=world["bob"])
    login(client, "desk")
    from test_flights import form_for
    assert form_for(glider)["tow_pilot_id"] == str(world["bob"].id)  # read from the tow flight
    client.post(f"/flights/{glider.id}", data=form_for(glider, launch_method="W"))
    db_session.refresh(glider)
    assert glider.tow_flight_id is None and glider.tow_glider_id is None


def test_aircraft_of_a_logged_flight_cannot_be_changed(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "desk")
    page = client.get(f"/flights/{f.id}").text
    assert 'name="glider_id" value="' in page and '<select name="glider_id"' not in page
    from test_flights import form_for
    client.post(f"/flights/{f.id}", data=form_for(f, glider_id=str(world["tow"].id)))
    db_session.refresh(f)
    assert f.glider_id == world["glider"].id


def test_pilot_opening_a_flight_without_pilot_gets_themselves_preselected(client, db_session, world):
    f = add_flight(db_session, world["glider"], None)
    login(client, "pia")
    page = client.get(f"/flights/{f.id}").text
    assert f'<option value="{world["pia"].id}" selected>Pia Pilot' in page
    login(client, "desk")
    page = client.get(f"/flights/{f.id}").text
    assert "selected>Pia Pilot" not in page


def test_day_arrows_skip_days_without_flights(client, db_session, world):
    from datetime import timedelta

    today = today_local()
    for days_ago in (30, 9):
        day = today - timedelta(days=days_ago)
        db_session.add(Flight(record_id=f"r-{days_ago}", glider_id=world["glider"].id, source=FlightSource.AUTO,
                              takeoff_time=combine_local(day, "12:00")))
    db_session.commit()
    login(client, "pia")
    page = client.get("/flugbuch").text  # today, no flights
    assert f'href="/flugbuch?datum={today - timedelta(days=9)}" title="Vorheriger Flugtag' in page
    page = client.get(f"/flugbuch?datum={today - timedelta(days=9)}").text
    assert f'href="/flugbuch?datum={today - timedelta(days=30)}" title="Vorheriger' in page
    assert f'href="/flugbuch?datum={today}" title="Nächster' in page  # nothing in between: back to today
    page = client.get(f"/flugbuch?datum={today - timedelta(days=30)}").text
    assert "Vorheriger Flugtag" not in page  # the first flying day


def test_calendar_shows_open_and_closed_days_and_links_them_to_the_flugbuch(client, db_session, world):
    from datetime import timedelta

    today = today_local()
    add_flight(db_session, world["glider"], world["pia"])
    closed_day = today - timedelta(days=400)  # another month, long ago
    db_session.add(Flight(record_id="old", glider_id=world["glider"].id, source=FlightSource.AUTO,
                          takeoff_time=combine_local(closed_day, "12:00"), landing_time=combine_local(closed_day, "13:00"),
                          finalized_at=datetime.now(timezone.utc)))
    db_session.commit()
    login(client, "admin")
    page = client.get("/admin/finalize").text
    assert f'class="cal-day cal-open cal-today" href="/flugbuch?datum={today}"' in page
    assert "Noch offene Tage" in page
    page = client.get(f"/admin/finalize?monat={closed_day:%Y-%m}").text
    assert f'class="cal-day cal-closed " href="/flugbuch?datum={closed_day}"' in page
    assert client.get("/admin/finalize?monat=kaputt").status_code == 200  # a mangled link shows this month


def test_admin_closes_and_reopens_a_day_from_its_flugbuch(client, db_session, world):
    f = add_flight(db_session, world["glider"], world["pia"])
    login(client, "admin")
    assert "Tag abschliessen" in client.get("/flugbuch").text
    response = client.post(f"/admin/finalize/{today_local()}", follow_redirects=False)
    assert response.headers["location"] == f"/flugbuch?datum={today_local()}"
    db_session.refresh(f)
    assert f.finalized_at is not None
    assert "Wieder öffnen" in client.get("/flugbuch").text
    login(client, "desk")
    assert "Tag abschliessen" not in client.get("/flugbuch").text and "Wieder öffnen" not in client.get("/flugbuch").text


def test_flugbuch_offers_checkout_for_each_pilot_of_the_day(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"])
    login(client, "desk")
    page = client.get("/flugbuch").text
    assert f'href="/auschecken/{world["pia"].id}?datum={today_local()}">Pia Pilot</a>' in page


def test_motor_glider_can_be_the_tow_aircraft(client, db_session, world):
    motor = Glider(registration="HB-2377", ogn_device_id="4B4DF0", kind=AircraftKind.MOTORGLIDER)
    db_session.add(motor)
    db_session.commit()
    login(client, "desk")
    page = client.get("/flugbuch").text
    tow_section = page[page.index('name="tow_glider_id"'):page.index("</fieldset>")]
    assert "HB-2377" in tow_section and "HB-1811" not in tow_section
    assert f'<option value="{world["tow"].id}" data-pilot="" selected>D-EDUY' in tow_section  # still the default

    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], launch_method="F",
                                                         tow_glider_id=str(motor.id)), follow_redirects=False)
    assert resp.status_code == 303
    assert db_session.query(Flight).filter_by(glider_id=world["glider"].id).one().tow_glider_id == motor.id
    # A glider can't tow.
    resp = client.post("/flugbuch", data=new_flight_form(world["glider"], launch_method="F",
                                                         tow_glider_id=str(world["glider"].id)))
    assert resp.status_code == 400 and "Bitte das Schleppflugzeug wählen." in resp.text
