from datetime import datetime, timedelta, timezone

from models import AircraftKind, Glider, GliderClaim, PilotStatus, Pilot


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
    assert "hb-1811 ist eingecheckt" in claim_resp.text.lower()

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
    assert claims[0].cancelled_at is not None
    assert claims[1].cancelled_at is None and claims[1].consumed_at is None


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


def make_glider(db_session, registration="HB-1811", kind=AircraftKind.GLIDER):
    glider = Glider(registration=registration, ogn_device_id=None, kind=kind)
    db_session.add(glider)
    db_session.commit()
    db_session.refresh(glider)
    return glider


def claims_of(db_session, glider):
    db_session.expire_all()
    return db_session.query(GliderClaim).filter_by(glider_id=glider.id).order_by(GliderClaim.id).all()


def test_whole_day_claim_expires_at_local_midnight(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    towplane = make_glider(db_session, "D-EDUY", AircraftKind.TOWPLANE)
    client.post(f"/claim/{towplane.claim_token}", data={"mode": "day"})
    [claim] = claims_of(db_session, towplane)
    assert claim.whole_day
    from timeutil import to_local
    expires = to_local(claim.expires_at.replace(tzinfo=timezone.utc))
    assert (expires.hour, expires.minute) == (0, 0)
    assert expires.date() == to_local(datetime.now(timezone.utc)).date() + timedelta(days=1)


def test_towplane_page_offers_whole_day_first(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    towplane = make_glider(db_session, "D-EDUY", AircraftKind.TOWPLANE)
    page = client.get(f"/claim/{towplane.claim_token}").text
    assert page.index("Für den ganzen Tag einchecken") < page.index("Nur für den nächsten Start")
    glider_page = client.get(f"/claim/{make_glider(db_session).claim_token}").text
    assert glider_page.index("Für den nächsten Start einchecken") < glider_page.index("Für den ganzen Tag")


def test_single_flight_claim_leaves_the_whole_day_claim_alone(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    towplane = make_glider(db_session, "D-EDUY", AircraftKind.TOWPLANE)
    client.post(f"/claim/{towplane.claim_token}", data={"mode": "day"})
    client.post(f"/claim/{towplane.claim_token}", data={"mode": "next"})
    day, single = claims_of(db_session, towplane)
    assert day.cancelled_at is None and single.cancelled_at is None


def test_new_whole_day_claim_replaces_everything(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    towplane = make_glider(db_session, "D-EDUY", AircraftKind.TOWPLANE)
    client.post(f"/claim/{towplane.claim_token}", data={"mode": "next"})
    client.post(f"/claim/{towplane.claim_token}", data={"mode": "day"})
    single, day = claims_of(db_session, towplane)
    assert single.cancelled_at is not None and day.cancelled_at is None


def test_pilot_can_release_own_claim(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    client.post(f"/claim/{glider.claim_token}")
    [claim] = claims_of(db_session, glider)
    assert "Freigeben" in client.get("/dashboard").text

    resp = client.post(f"/claims/{claim.id}/release", headers={"referer": "http://testserver/dashboard"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/dashboard"
    [claim] = claims_of(db_session, glider)
    assert claim.cancelled_at is not None
    assert "Freigeben" not in client.get("/dashboard").text


def test_pilot_cannot_release_someone_elses_claim(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    client.post(f"/claim/{glider.claim_token}")
    [claim] = claims_of(db_session, glider)
    client.post("/logout")
    client.post("/signup", data={"full_name": "Bob Pilot", "email": "bob@example.com", "password": "password123"})
    approve(db_session, "bob@example.com")
    client.post("/login", data={"email": "bob@example.com", "password": "password123"})

    assert client.post(f"/claims/{claim.id}/release").status_code == 404
    [claim] = claims_of(db_session, glider)
    assert claim.cancelled_at is None


def test_release_never_redirects_off_site(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    client.post(f"/claim/{glider.claim_token}")
    [claim] = claims_of(db_session, glider)
    resp = client.post(f"/claims/{claim.id}/release", headers={"referer": "https://evil.example/x"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/claim"


def test_picker_shows_who_is_checked_in(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    client.post(f"/claim/{glider.claim_token}")
    assert "Alice eingecheckt" in client.get("/claim").text
