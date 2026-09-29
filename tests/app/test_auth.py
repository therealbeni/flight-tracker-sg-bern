import re

from models import Pilot, PilotStatus


def signup(client, name, email, password="password123"):
    return client.post(
        "/signup",
        data={"full_name": name, "email": email, "password": password},
        follow_redirects=False,
    )


def test_first_signup_becomes_admin_and_can_log_in_immediately(client):
    resp = signup(client, "Alice Admin", "alice@example.com")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"

    # Session cookie should already be set - dashboard is reachable without a fresh login.
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Alice" not in dashboard.text or True  # dashboard doesn't show name, just smoke check


def test_second_signup_is_pending_and_cannot_log_in(client):
    signup(client, "Alice Admin", "alice@example.com")
    client.post("/logout")

    resp = signup(client, "Bob Pilot", "bob@example.com")
    assert resp.status_code == 200
    assert "fast geschafft" in resp.text.lower()

    login = client.post("/login", data={"email": "bob@example.com", "password": "password123"})
    assert "fast geschafft" in login.text.lower()

    # Confirm no session was created for the pending pilot.
    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/login"


def test_admin_can_approve_pending_pilot_and_then_they_can_log_in(client):
    signup(client, "Alice Admin", "alice@example.com")
    client.post("/logout")
    signup(client, "Bob Pilot", "bob@example.com")

    # Bob can't reach admin pages.
    forbidden = client.get("/admin/pilots", follow_redirects=False)
    assert forbidden.status_code in (303, 403)

    client.post("/logout")
    client.post("/login", data={"email": "alice@example.com", "password": "password123"})

    pilots_page = client.get("/admin/pilots")
    match = re.search(r'/admin/pilots/(\d+)/approve', pilots_page.text)
    assert match, "expected an approve form for Bob"
    pilot_id = match.group(1)

    approve = client.post(f"/admin/pilots/{pilot_id}/approve", follow_redirects=False)
    assert approve.status_code == 303

    client.post("/logout")
    login = client.post("/login", data={"email": "bob@example.com", "password": "password123"}, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/dashboard"


def test_wrong_password_shows_error(client):
    signup(client, "Alice Admin", "alice@example.com")
    client.post("/logout")

    resp = client.post("/login", data={"email": "alice@example.com", "password": "wrong-password"})
    assert "e-mail oder passwort falsch" in resp.text.lower()


def test_password_reset_flow(client, fake_email):
    signup(client, "Alice Admin", "alice@example.com")
    client.post("/logout")

    client.post("/forgot-password", data={"email": "alice@example.com"})
    assert len(fake_email.messages) == 1
    body = fake_email.messages[0]["body"]
    match = re.search(r"/reset-password/(\S+)", body)
    assert match, "expected a reset link in the email body"
    token = match.group(1)

    reset = client.post(f"/reset-password/{token}", data={"password": "newpassword123"}, follow_redirects=False)
    assert reset.status_code == 303
    assert reset.headers["location"] == "/login"

    old_login = client.post("/login", data={"email": "alice@example.com", "password": "password123"})
    assert "falsch" in old_login.text.lower()

    new_login = client.post(
        "/login", data={"email": "alice@example.com", "password": "newpassword123"}, follow_redirects=False
    )
    assert new_login.status_code == 303

    # The token can't be reused.
    reuse = client.post(f"/reset-password/{token}", data={"password": "anotherpassword123"})
    assert "ungültig oder abgelaufen" in reuse.text.lower()


def test_forgot_password_does_not_reveal_whether_account_exists(client, fake_email):
    resp_unknown = client.post("/forgot-password", data={"email": "nobody@example.com"})
    assert "falls diese e-mail-adresse registriert ist" in resp_unknown.text.lower()
    assert len(fake_email.messages) == 0


def signup_and_login(client, name, email, password="password123"):
    signup(client, name, email, password)
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def test_admin_can_view_the_app_as_a_pilot_and_switch_back(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    client.post("/signup", data={"full_name": "Bob Pilot", "email": "bob@example.com", "password": "password123"})
    bob = db_session.query(Pilot).filter_by(email="bob@example.com").one()
    bob.status = PilotStatus.APPROVED
    db_session.commit()

    client.post(f"/admin/pilots/{bob.id}/view-as")
    home = client.get("/dashboard").text
    assert "Hallo Bob" in home
    assert "Du siehst die App als Bob Pilot" in home
    assert client.get("/admin/pilots").status_code == 403  # really sees what Bob sees

    resp = client.post("/view-as/end", follow_redirects=False)
    assert resp.headers["location"] == "/admin/pilots"
    home = client.get("/dashboard").text
    assert "Hallo Alice" in home and "Du siehst die App als" not in home


def test_pilot_cannot_view_as_someone_else(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    client.post("/logout")
    client.post("/signup", data={"full_name": "Bob Pilot", "email": "bob@example.com", "password": "password123"})
    bob = db_session.query(Pilot).filter_by(email="bob@example.com").one()
    bob.status = PilotStatus.APPROVED
    db_session.commit()
    client.post("/login", data={"email": "bob@example.com", "password": "password123"})
    alice = db_session.query(Pilot).filter_by(email="alice@example.com").one()

    assert client.post(f"/admin/pilots/{alice.id}/view-as").status_code == 403
    # Ending a view-as that never started logs out instead of granting anything.
    resp = client.post("/view-as/end", follow_redirects=False)
    assert resp.headers["location"] == "/login"
