"""The Flugdienstleiter (FDL): the Flugbuch as command center with the day's
aircraft and pilots beside it, checking pilots in at the club PC (no
check-ins of its own), and checkouts that tell the FDL who has gone home."""

import pytest

from models import AircraftKind, Checkout, Glider, GliderClaim
from test_flugbuch import add_flight, login, world  # noqa: F401 - world is a fixture
from timeutil import today_local


def panel(client) -> str:
    """The Flugdienst side panel of the Flugbuch."""
    page = client.get("/flugbuch").text
    return page[page.index('id="flugdienst"'):page.index("</aside>")]


def claim(client, glider, mode="next"):
    return client.post(f"/claim/{glider.claim_token}", data={"mode": mode})


def checkout(client, pilot, **data):
    return client.post(f"/auschecken/{pilot.id}", data={"day": str(today_local()), **data}, follow_redirects=False)


def test_overview_shows_aircraft_in_the_air_checked_in_and_free(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end=None)  # HB-1811 flying
    login(client, "bob")
    claim(client, world["tow"], mode="day")
    login(client, "desk")
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/flugbuch"
    page = client.get("/flugbuch").text
    assert "Flugdienst heute" in page
    assert "strip strip-air" in page and "In der Luft, " in page and "Pia Pilot" in page
    assert "strip strip-claimed" in page and "Bob Brunner, ganzer Tag" in page
    assert 'href="/claim"' not in page and "Meine Flüge" not in page  # navigation without own check-in


def test_overview_lists_pilots_present_and_gone_home(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end="10:40")
    add_flight(db_session, world["glider"], world["bob"], start="11:00", end="11:30")
    login(client, "bob")
    checkout(client, world["bob"])
    login(client, "desk")
    present, gone = panel(client).split("Nach Hause gegangen")
    assert "Pia Pilot" in present and "Bob Brunner" not in present
    assert "Bob Brunner" in gone and "Ausgecheckt um" in gone


def test_fdl_cannot_check_in(client, db_session, world):
    login(client, "desk")
    assert client.get("/claim").status_code == 403
    assert client.get(f"/claim/{world['glider'].claim_token}").status_code == 403
    assert claim(client, world["glider"]).status_code == 403
    assert db_session.query(GliderClaim).count() == 0
    assert client.get("/logbook", follow_redirects=False).headers["location"] == "/dashboard"


def test_fdl_releases_a_forgotten_check_in(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    the_claim = db_session.query(GliderClaim).one()
    login(client, "desk")
    client.post(f"/claims/{the_claim.id}/release")
    db_session.refresh(the_claim)
    assert the_claim.cancelled_at is not None


def test_other_pilots_cannot_release_my_check_in(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    the_claim = db_session.query(GliderClaim).one()
    login(client, "bob")
    assert client.post(f"/claims/{the_claim.id}/release").status_code == 404
    db_session.refresh(the_claim)
    assert the_claim.cancelled_at is None


def test_checkout_is_recorded_and_checking_in_again_brings_the_pilot_back(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    checkout(client, world["pia"])
    checkout(client, world["pia"])  # double tap: no error, still one checkout
    assert db_session.query(Checkout).count() == 1
    assert "Du bist ausgecheckt" in client.get("/dashboard").text

    claim(client, world["glider"])  # flies once more after all
    assert "Du bist ausgecheckt" not in client.get("/dashboard").text
    login(client, "desk")
    page = panel(client)
    assert "Nach Hause gegangen" not in page and "Eingecheckt auf HB-1811" in page


def test_fdl_checks_out_a_pilot_and_returns_to_the_flugbuch(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end="10:40")
    login(client, "desk")
    assert f'href="/auschecken/{world["pia"].id}?next=/flugbuch"' in client.get("/flugbuch").text
    assert 'name="next" value="/flugbuch"' in client.get(f"/auschecken/{world['pia'].id}?next=/flugbuch").text
    assert checkout(client, world["pia"], next="/flugbuch").headers["location"] == "/flugbuch"
    the_checkout = db_session.query(Checkout).one()
    assert the_checkout.pilot_id == world["pia"].id and the_checkout.by_pilot_id == world["desk"].id


def test_checkout_warns_while_the_pilot_is_still_in_the_air(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end=None)
    login(client, "pia")
    assert "noch in der Luft" in client.get(f"/auschecken/{world['pia'].id}").text


def test_the_fdl_account_itself_cannot_be_checked_out(client, db_session, world):
    login(client, "admin")
    assert client.get(f"/auschecken/{world['desk'].id}").status_code == 404


# ---- Einchecken at the club PC


def desk_check_in(client, pilot, glider, **data):
    return client.post("/flugbuch/einchecken", data={"pilot_id": str(pilot.id), "glider_id": str(glider.id), **data},
                       follow_redirects=False)


def test_fdl_checks_a_pilot_in_on_the_club_pc(client, db_session, world):
    login(client, "desk")
    resp = desk_check_in(client, world["pia"], world["glider"], mode="next")
    assert resp.status_code == 303
    the_claim = db_session.query(GliderClaim).one()
    assert (the_claim.pilot_id, the_claim.glider_id, the_claim.whole_day) == (world["pia"].id, world["glider"].id, False)
    assert the_claim.claimed_by_id == world["desk"].id
    assert "Pia Pilot ist auf HB-1811 eingecheckt (nächster Start)" in client.get(resp.headers["location"]).text
    login(client, "pia")
    assert "HB-1811" in client.get("/dashboard").text  # her phone shows it like her own check-in


def test_desk_check_in_defaults_to_the_whole_day_on_the_tow_plane(client, db_session, world):
    login(client, "desk")
    desk_check_in(client, world["bob"], world["tow"])  # without JavaScript no choice is sent
    assert db_session.query(GliderClaim).one().whole_day


def test_desk_check_in_asks_before_taking_over(client, db_session, world):
    login(client, "pia")
    claim(client, world["glider"])
    login(client, "desk")
    resp = desk_check_in(client, world["bob"], world["glider"], mode="next")
    assert resp.status_code == 409
    assert "Pia Pilot ist schon auf HB-1811 eingecheckt" in resp.text and 'name="takeover"' in resp.text
    assert db_session.query(GliderClaim).filter(GliderClaim.cancelled_at.is_(None)).count() == 1

    assert desk_check_in(client, world["bob"], world["glider"], mode="next", takeover="1").status_code == 303
    pias, bobs = db_session.query(GliderClaim).order_by(GliderClaim.id).all()
    assert pias.cancelled_by_id == world["bob"].id  # Pia is told Bob took it over
    assert bobs.cancelled_at is None
    login(client, "pia")
    assert "Bob Brunner hat HB-1811 übernommen" in client.get("/dashboard").text


@pytest.mark.parametrize("data, message", [
    ({"pilot_id": ""}, "Bitte den Piloten wählen."),
    ({"pilot_id": "99999"}, "Bitte den Piloten wählen."),
    ({"pilot_id": "DESK"}, "Bitte den Piloten wählen."),
    ({"glider_id": "x"}, "Bitte das Flugzeug wählen."),
])
def test_desk_check_in_needs_a_pilot_and_an_aircraft(client, db_session, world, data, message):
    login(client, "desk")
    data = {k: str(world["desk"].id) if v == "DESK" else v for k, v in data.items()}
    resp = client.post("/flugbuch/einchecken", data={"pilot_id": str(world["pia"].id),
                                                       "glider_id": str(world["glider"].id), **data})
    assert resp.status_code == 400 and message in resp.text
    assert db_session.query(GliderClaim).count() == 0


def test_pilots_cannot_check_others_in(client, db_session, world):
    login(client, "pia")
    assert desk_check_in(client, world["bob"], world["glider"]).status_code == 403
    assert "/flugbuch/einchecken" not in client.get("/flugbuch").text


# ---- private aircraft


@pytest.fixture
def private(db_session, world):
    """Bob's own glider."""
    glider = Glider(registration="HB-3407", model="LS 8", ogn_device_id="4B5407", kind=AircraftKind.GLIDER,
                    owners=[world["bob"]])
    db_session.add(glider)
    db_session.commit()
    return glider


def test_private_aircraft_are_listed_for_everyone(client, db_session, world, private):
    # Often shared by several members: anyone may check in on it.
    login(client, "pia")
    assert "LS 8, privat" in client.get("/claim").text
    claim(client, private)
    assert db_session.query(GliderClaim).one().glider_id == private.id


def test_private_aircraft_is_never_free_on_the_club_pc(client, db_session, world, private):
    login(client, "desk")
    assert "HB-3407" not in panel(client)  # not in use: not shown
    assert "HB-3407 (LS 8), privat - frei" not in client.get("/flugbuch").text  # never "frei"...
    assert "HB-3407 (LS 8), privat" in client.get("/flugbuch").text  # ...but offered at check-in
    desk_check_in(client, world["bob"], private, mode="next")
    shown = panel(client)
    assert "HB-3407" in shown and "privat" in shown and "Bob Brunner, nächster Start" in shown
    add_flight(db_session, private, world["bob"], start="10:00", end="12:00")
    shown = panel(client)
    assert "HB-3407" in shown  # flew today
    assert shown.count("strip strip-free") == 2  # HB-1811 and D-EDUY, never the private one


def test_private_motor_glider_is_a_tow_plane_choice(client, db_session, world):
    db_session.add(Glider(registration="HB-2999", kind=AircraftKind.MOTORGLIDER, owners=[world["bob"]]))
    db_session.commit()
    login(client, "desk")
    page = client.get("/flugbuch?neu=1").text
    tow_select = page[page.index('name="tow_glider_id"'):]
    tow_select = tow_select[:tow_select.index("</select>")]
    assert "D-EDUY" in tow_select and "HB-2999" in tow_select


def test_admin_makes_an_aircraft_private_and_back(client, db_session, world):
    login(client, "admin")
    glider = world["glider"]
    form = {"registration": "HB-1811", "model": "ASK 21", "kind": "glider", "ogn_device_id": "4B4BBA",
            "new_owner_id": str(world["pia"].id)}
    assert client.post(f"/admin/gliders/{glider.id}", data=form, follow_redirects=False).status_code == 303
    db_session.refresh(glider)
    assert [o.id for o in glider.owners] == [world["pia"].id] and glider.model == "ASK 21"
    form = {**form, "owner_ids": [str(world["pia"].id)], "new_owner_id": str(world["bob"].id)}
    client.post(f"/admin/gliders/{glider.id}", data=form)
    db_session.refresh(glider)
    assert {o.id for o in glider.owners} == {world["pia"].id, world["bob"].id}
    client.post(f"/admin/gliders/{glider.id}", data={**form, "owner_ids": [], "new_owner_id": ""})
    db_session.refresh(glider)
    assert glider.owners == []  # the club's again


def test_admin_cannot_give_an_aircraft_an_existing_registration(client, db_session, world):
    login(client, "admin")
    resp = client.post(f"/admin/gliders/{world['glider'].id}", data={"registration": "D-EDUY", "kind": "glider"})
    assert resp.status_code == 400 and "Ein Flugzeug D-EDUY gibt es schon." in resp.text
