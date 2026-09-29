from datetime import datetime, timedelta, timezone

from models import Airfield, Flight, FlightSource, Glider, Pilot, PilotStatus


def signup_and_login(client, name, email, password="password123"):
    client.post("/signup", data={"full_name": name, "email": email, "password": password})
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def approve(client, db_session, email):
    pilot = db_session.query(Pilot).filter_by(email=email).one()
    pilot.status = PilotStatus.APPROVED
    db_session.commit()
    return pilot


def make_flight(db_session, glider, pilot_id=None, takeoff_offset_hours=0, landed=True):
    takeoff = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(
        hours=takeoff_offset_hours
    )
    flight = Flight(
        record_id=f"rec-{takeoff.isoformat()}-{glider.id}-{pilot_id}",
        glider_id=glider.id,
        pilot_id=pilot_id,
        takeoff_time=takeoff,
        landing_time=takeoff + timedelta(minutes=20) if landed else None,
        duration_min=20.0 if landed else None,
        source=FlightSource.AUTO,
    )
    db_session.add(flight)
    db_session.commit()
    db_session.refresh(flight)
    return flight


def make_glider(db_session, registration="HB-1811"):
    glider = Glider(registration=registration, ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    db_session.refresh(glider)
    return glider


def form_for(flight, **overrides):
    """What the browser submits: the flight's current values, plus changes."""
    from flight_form import FlightInput
    data = {k: v for k, v in vars(FlightInput.from_flight(flight)).items() if k != "errors"}
    data.update(overrides)
    return data


def make_airfields(db_session, *icaos):
    for icao in icaos:
        if db_session.get(Airfield, icao) is None:
            db_session.add(Airfield(icao=icao, name=icao, latitude=0.0, longitude=0.0, elevation_m=0.0))
    db_session.commit()


def test_pilot_can_claim_an_unclaimed_flight_as_their_own(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")  # first signup = admin
    glider = make_glider(db_session)
    make_airfields(db_session, "LSZB")
    flight = make_flight(db_session, glider, pilot_id=None)

    resp = client.post(
        f"/flights/{flight.id}",
        data=form_for(flight, takeoff_airfield_icao="LSZB", landing_airfield_icao="LSZB"),
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.refresh(flight)
    assert flight.takeoff_airfield_icao == "LSZB"
    assert flight.verified_by_pilot is True


def test_pilot_cannot_edit_someone_elses_flight(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    admin = db_session.query(Pilot).filter_by(email="alice@example.com").one()
    client.post("/logout")

    signup_and_login(client, "Bob Pilot", "bob@example.com")
    approve(client, db_session, "bob@example.com")
    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "password123"})

    glider = make_glider(db_session)
    flight = make_flight(db_session, glider, pilot_id=admin.id)  # belongs to Alice

    detail = client.get(f"/flights/{flight.id}")
    assert detail.status_code == 200
    assert "gehört einem anderen piloten" in detail.text.lower()

    resp = client.post(f"/flights/{flight.id}", data=form_for(flight, takeoff_airfield_icao="LSZB"))
    assert resp.status_code == 403


def test_non_admin_cannot_reassign_flight_to_someone_else(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    client.post("/logout")
    signup_and_login(client, "Bob Pilot", "bob@example.com")
    bob = approve(client, db_session, "bob@example.com")
    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "password123"})

    glider = make_glider(db_session)
    flight = make_flight(db_session, glider, pilot_id=None)

    other_pilot_id = bob.id + 999  # doesn't matter if it exists, should be rejected either way
    resp = client.post(f"/flights/{flight.id}", data=form_for(flight, pilot_id=str(other_pilot_id)))
    assert resp.status_code == 400
    db_session.refresh(flight)
    assert flight.pilot_id is None


def test_verify_without_changes_sets_verified_flag(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    flight = make_flight(db_session, glider, pilot_id=None)

    resp = client.post(f"/flights/{flight.id}/verify", follow_redirects=False)
    assert resp.status_code == 303
    db_session.refresh(flight)
    assert flight.verified_by_pilot is True


def test_admin_finalize_locks_editing_and_unlock_reopens_it(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    flight = make_flight(db_session, glider, pilot_id=None)
    day = flight.takeoff_time.date().isoformat()

    overview = client.get("/admin/finalize")
    assert flight.takeoff_time.strftime("%d.%m.%Y") in overview.text

    client.post(f"/admin/finalize/{day}", follow_redirects=False)
    db_session.refresh(flight)
    assert flight.finalized_at is not None

    resp = client.post(f"/flights/{flight.id}/verify")
    assert resp.status_code == 403

    client.post(f"/admin/finalize/{day}/unlock", follow_redirects=False)
    db_session.refresh(flight)
    assert flight.finalized_at is None

    resp = client.post(f"/flights/{flight.id}/verify", follow_redirects=False)
    assert resp.status_code == 303


def test_non_admin_cannot_access_finalize_page(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    client.post("/logout")
    signup_and_login(client, "Bob Pilot", "bob@example.com")
    approve(client, db_session, "bob@example.com")
    client.post("/logout")
    client.post("/login", data={"email": "bob@example.com", "password": "password123"})

    resp = client.get("/admin/finalize")
    assert resp.status_code == 403


def test_unknown_airfield_code_is_rejected_with_a_friendly_error(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    flight = make_flight(db_session, glider, pilot_id=None)

    resp = client.post(
        f"/flights/{flight.id}",
        data=form_for(flight, takeoff_airfield_icao="ZZZZ"),
    )
    assert resp.status_code == 400
    assert "kennen wir noch nicht" in resp.text

    db_session.refresh(flight)
    assert flight.takeoff_airfield_icao is None  # rejected, not silently saved
    assert flight.verified_by_pilot is False


def test_editing_records_audit_history(client, db_session):
    signup_and_login(client, "Alice Admin", "alice@example.com")
    glider = make_glider(db_session)
    make_airfields(db_session, "LSZB", "LSTZ")
    flight = make_flight(db_session, glider, pilot_id=None)

    client.post(
        f"/flights/{flight.id}",
        data=form_for(flight, takeoff_airfield_icao="LSZB", landing_airfield_icao="LSTZ",
                      notes="outlanding, retrieved by car"),
    )

    detail = client.get(f"/flights/{flight.id}")
    assert "Landeort" in detail.text and "Alice Admin" in detail.text
    assert "LSTZ" in detail.text
    assert "outlanding, retrieved by car" in detail.text
