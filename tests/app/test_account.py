"""Konto: everyone changes their own name, e-mail address and password."""

from models import AircraftKind, Glider
from security import verify_password
from test_flugbuch import PASSWORD, login, world  # noqa: F401 - world is a fixture


def test_change_name(client, db_session, world):
    login(client, "pia")
    resp = client.post("/konto/profil", data={"full_name": "  Pia   Muster ", "email": "pia@example.com"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/konto?gespeichert=profil"
    db_session.refresh(world["pia"])
    assert world["pia"].full_name == "Pia Muster"
    assert "Name und E-Mail gespeichert" in client.get("/konto?gespeichert=profil").text


def test_changing_the_email_needs_the_password(client, db_session, world):
    login(client, "pia")
    resp = client.post("/konto/profil", data={"full_name": "Pia Pilot", "email": "neu@example.com"})
    assert resp.status_code == 400 and "aktuelles Passwort" in resp.text
    client.post("/konto/profil", data={"full_name": "Pia Pilot", "email": "Neu@Example.com",
                                       "current_password": PASSWORD})
    db_session.refresh(world["pia"])
    assert world["pia"].email == "neu@example.com"
    client.post("/logout")
    assert client.post("/login", data={"email": "neu@example.com", "password": PASSWORD},
                       follow_redirects=False).headers["location"] == "/dashboard"


def test_email_of_another_account_is_refused(client, db_session, world):
    login(client, "pia")
    resp = client.post("/konto/profil", data={"full_name": "Pia Pilot", "email": "bob@example.com",
                                              "current_password": PASSWORD})
    assert resp.status_code == 400 and "gibt es schon ein Konto" in resp.text
    db_session.refresh(world["pia"])
    assert world["pia"].email == "pia@example.com"


def test_change_password_keeps_this_session_and_ends_the_others(client, db_session, world):
    from fastapi.testclient import TestClient
    import main
    other = TestClient(main.app)
    other.post("/login", data={"email": "pia@example.com", "password": PASSWORD})
    login(client, "pia")
    resp = client.post("/konto/passwort", data={"current_password": PASSWORD, "new_password": "neues-passwort",
                                                "new_password_again": "neues-passwort"}, follow_redirects=False)
    assert resp.headers["location"] == "/konto?gespeichert=passwort"
    db_session.refresh(world["pia"])
    assert verify_password("neues-passwort", world["pia"].password_hash)
    assert client.get("/konto", follow_redirects=False).status_code == 200  # still logged in here
    assert other.get("/konto", follow_redirects=False).status_code == 303  # logged out elsewhere


def test_password_change_is_checked(client, db_session, world):
    login(client, "pia")
    for data, message in [
        ({"current_password": "falsch", "new_password": "neues-passwort", "new_password_again": "neues-passwort"},
         "Das aktuelle Passwort stimmt nicht."),
        ({"current_password": PASSWORD, "new_password": "neues-passwort", "new_password_again": "anderes"},
         "nicht gleich"),
        ({"current_password": PASSWORD, "new_password": "kurz", "new_password_again": "kurz"}, "mindestens 8 Zeichen"),
    ]:
        resp = client.post("/konto/passwort", data=data)
        assert resp.status_code == 400 and message in resp.text
    db_session.refresh(world["pia"])
    assert verify_password(PASSWORD, world["pia"].password_hash)


def test_owners_see_their_aircraft_and_its_qr_code(client, db_session, world):
    own = Glider(registration="HB-3407", ogn_device_id="4B5407", kind=AircraftKind.GLIDER, owners=[world["bob"]])
    db_session.add(own)
    db_session.commit()
    login(client, "bob")
    assert "HB-3407" in client.get("/konto").text
    assert client.get(f"/konto/flugzeuge/{own.id}/qr.png").headers["content-type"] == "image/png"
    login(client, "pia")
    assert "Meine Flugzeuge" not in client.get("/konto").text
    assert client.get(f"/konto/flugzeuge/{own.id}/qr.png").status_code == 404


def test_account_page_needs_a_login(client, world):
    assert client.get("/konto", follow_redirects=False).headers["location"].startswith("/login")
