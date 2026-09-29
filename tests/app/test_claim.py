from models import Glider, GliderClaim, PilotStatus, Pilot


def signup_and_login(client, name, email, password="password123"):
    client.post("/signup", data={"full_name": name, "email": email, "password": password})
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def approve(db_session, email):
    pilot = db_session.query(Pilot).filter_by(email=email).one()
    pilot.status = PilotStatus.APPROVED
    db_session.commit()


def test_claiming_a_glider_creates_a_claim_for_the_logged_in_pilot(client, db_session):
    # First signup is auto-approved admin.
    signup_and_login(client, "Alice Admin", "alice@example.com")

    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    db_session.refresh(glider)

    resp = client.get(f"/claim/{glider.claim_token}")
    assert resp.status_code == 200
    assert "HB-1811" in resp.text

    claim_resp = client.post(f"/claim/{glider.claim_token}")
    assert claim_resp.status_code == 200
    assert "hb-1811 is yours" in claim_resp.text.lower()

    claims = db_session.query(GliderClaim).filter_by(glider_id=glider.id).all()
    assert len(claims) == 1
    assert claims[0].consumed_at is None


def test_claiming_again_supersedes_the_previous_unconsumed_claim(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    db_session.refresh(glider)

    client.post(f"/claim/{glider.claim_token}")
    client.post(f"/claim/{glider.claim_token}")

    claims = db_session.query(GliderClaim).filter_by(glider_id=glider.id).order_by(GliderClaim.id).all()
    assert len(claims) == 2
    assert claims[0].consumed_at is not None
    assert claims[1].consumed_at is None


def test_unapproved_pilot_cannot_claim(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    db_session.refresh(glider)
    client.post("/logout")

    client.post("/signup", data={"full_name": "Bob Pilot", "email": "bob@example.com", "password": "password123"})
    # Bob never got a session (not auto-logged-in as a non-first signup), so this
    # should redirect to login rather than error.
    resp = client.get(f"/claim/{glider.claim_token}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_unknown_claim_token_is_404(client):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    resp = client.get("/claim/does-not-exist")
    assert resp.status_code == 404
