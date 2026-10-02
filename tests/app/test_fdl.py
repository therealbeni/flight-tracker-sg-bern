"""The Flugdienstleiter (FDL): the day's overview, no check-ins of its own,
and checkouts that tell the FDL who has gone home."""

from models import Checkout, GliderClaim
from test_flugbuch import add_flight, login, world  # noqa: F401 - world is a fixture
from timeutil import today_local


def claim(client, glider, mode="next"):
    return client.post(f"/claim/{glider.claim_token}", data={"mode": mode})


def checkout(client, pilot, **data):
    return client.post(f"/auschecken/{pilot.id}", data={"day": str(today_local()), **data}, follow_redirects=False)


def test_overview_shows_aircraft_in_the_air_checked_in_and_free(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end=None)  # HB-1811 flying
    login(client, "bob")
    claim(client, world["tow"], mode="day")
    login(client, "desk")
    page = client.get("/dashboard").text
    assert "Flugdienst" in page
    assert "strip strip-air" in page and "In der Luft seit" in page and "Pia Pilot" in page
    assert "strip strip-claimed" in page and "Bob Brunner, ganzer Tag" in page
    assert ">Einchecken<" not in page and "Meine Flüge" not in page  # navigation without check-in


def test_overview_lists_pilots_present_and_gone_home(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end="10:40")
    add_flight(db_session, world["glider"], world["bob"], start="11:00", end="11:30")
    login(client, "bob")
    checkout(client, world["bob"])
    login(client, "desk")
    present, gone = client.get("/dashboard").text.split("Nach Hause gegangen")
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
    page = client.get("/dashboard").text
    assert "Nach Hause gegangen" not in page and "Eingecheckt auf HB-1811" in page


def test_fdl_checks_out_a_pilot_and_returns_to_the_overview(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end="10:40")
    login(client, "desk")
    assert 'name="next" value="/dashboard"' in client.get(f"/auschecken/{world['pia'].id}?next=/dashboard").text
    assert checkout(client, world["pia"], next="/dashboard").headers["location"] == "/dashboard"
    the_checkout = db_session.query(Checkout).one()
    assert the_checkout.pilot_id == world["pia"].id and the_checkout.by_pilot_id == world["desk"].id


def test_checkout_warns_while_the_pilot_is_still_in_the_air(client, db_session, world):
    add_flight(db_session, world["glider"], world["pia"], start="10:00", end=None)
    login(client, "pia")
    assert "noch in der Luft" in client.get(f"/auschecken/{world['pia'].id}").text


def test_the_fdl_account_itself_cannot_be_checked_out(client, db_session, world):
    login(client, "admin")
    assert client.get(f"/auschecken/{world['desk'].id}").status_code == 404
