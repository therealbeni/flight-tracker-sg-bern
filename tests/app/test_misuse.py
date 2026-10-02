"""Misuse: guessing passwords, forged forms, junk input. Nothing may crash
(500) or leak, whatever is sent to whichever page, logged in or not."""

import itertools

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import main as main_module
from models import Airfield, Flight, Glider, Pilot, PilotRole, PilotStatus
from security import LoginThrottle, hash_password, login_throttle

PASSWORD = "password123"


@pytest.fixture(autouse=True)
def fresh_throttle():
    login_throttle._failures.clear()
    yield
    login_throttle._failures.clear()


@pytest.fixture
def people(db_session):
    made = {}
    for key, role in [("admin", PilotRole.ADMIN), ("pilot", PilotRole.PILOT), ("desk", PilotRole.FLIGHTDESK)]:
        made[key] = Pilot(full_name=f"{key.title()} Test", email=f"{key}@example.com",
                          password_hash=hash_password(PASSWORD), role=role, status=PilotStatus.APPROVED)
    db_session.add_all(made.values())
    db_session.add(Airfield(icao="LSZB", name="Bern", latitude=46.9, longitude=7.5, elevation_m=510))
    glider = Glider(registration="HB-1811", ogn_device_id="4B4BBA")
    db_session.add(glider)
    db_session.commit()
    return made


def login(client, who, password=PASSWORD):
    return client.post("/login", data={"email": f"{who}@example.com", "password": password}, follow_redirects=False)


def test_password_guessing_is_slowed_down(client, people):
    for _ in range(10):
        assert login(client, "pilot", "wrong-guess").status_code == 400
    blocked = login(client, "pilot")  # even the right password, for now
    assert blocked.status_code == 429
    assert "Zu viele Versuche" in blocked.text


def test_throttle_forgets_after_the_window():
    throttle = LoginThrottle(limit=3, window_s=60)
    for t in range(3):
        throttle.failed("a@b.ch", "1.2.3.4", now=t)
    assert throttle.blocked("a@b.ch", "9.9.9.9", now=10)  # same account, other IP
    assert throttle.blocked("x@y.ch", "1.2.3.4", now=10)  # same IP, other account
    assert not throttle.blocked("a@b.ch", "1.2.3.4", now=100)


def test_successful_login_resets_the_count(client, people):
    for _ in range(9):
        login(client, "pilot", "wrong")
    assert login(client, "pilot").status_code == 303
    client.post("/logout")
    assert login(client, "pilot", "wrong").status_code == 400  # not blocked


@pytest.mark.parametrize("password, message", [("kurz", "mindestens 8"), ("ä" * 40, "höchstens 72")])
def test_unusable_passwords_are_explained(client, people, password, message):
    resp = client.post("/signup", data={"full_name": "Neu Ling", "email": "neu@example.com", "password": password})
    assert resp.status_code == 400 and message in resp.text


def test_signup_needs_a_real_name(client, people):
    resp = client.post("/signup", data={"full_name": "   ", "email": "neu@example.com", "password": PASSWORD})
    assert resp.status_code == 400


def test_forms_posted_from_another_site_are_refused(client, people):
    login(client, "admin")
    resp = client.post("/admin/gliders", data={"registration": "HB-6666"}, headers={"origin": "https://evil.example"})
    assert resp.status_code == 403
    ok = client.post("/admin/gliders", data={"registration": "HB-6666"}, headers={"origin": "http://testserver"},
                     follow_redirects=False)
    assert ok.status_code == 303


def test_security_headers(client):
    headers = client.get("/login").headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"


def test_admin_cannot_reject_themselves(client, db_session, people):
    login(client, "admin")
    client.post(f"/admin/pilots/{people['admin'].id}/reject")
    db_session.refresh(people["admin"])
    assert people["admin"].status is PilotStatus.APPROVED


def test_mangled_links_get_a_german_page(client, people):
    login(client, "pilot")
    resp = client.get("/flights/abc")
    assert resp.status_code == 400 and "Ungültige Eingabe" in resp.text


# ---- fuzzing every route

JUNK_PATH_VALUES = ["0", "999999", "abc", "-1", "2026-13-45", "..%2F..%2Fetc", "' OR 1=1 --"]
JUNK_FORM = {
    "email": "x' OR '1'='1", "password": "", "full_name": "<script>alert(1)</script>", "pilot_id": "abc",
    "glider_id": "-5", "companion_id": "1e9", "launch_method": "X", "takeoff_time": "99:99", "landing_time": "--",
    "landings": "-1", "day": "gestern", "mode": "forever", "role": "superuser", "kind": "rocket",
    "latitude": "north", "longitude": "", "icao": "A" * 300, "name": "B" * 5000, "registration": "",
    "notes": "C" * 5000, "next": "https://evil.example", "takeoff_airfield_icao": "' ; DROP TABLE flights; --",
}


def routes():
    for route in main_module.app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield method, route.path


def fill(path, value):
    import re
    return re.sub(r"\{[^}]+\}", value, path)


@pytest.mark.parametrize("who", [None, "pilot", "desk", "admin"])
def test_no_route_crashes_on_junk(db_session, people, who):
    client = TestClient(main_module.app, raise_server_exceptions=False)
    # Something to point at: a flight and a claim that exist.
    flight = Flight(record_id="f1", glider_id=1, pilot_id=people["pilot"].id)
    db_session.add(flight)
    db_session.commit()
    if who:
        login(client, who)
    crashes = []
    for (method, path), value in itertools.product(sorted(set(routes())), ["1", *JUNK_PATH_VALUES]):
        url = fill(path, value)
        resp = client.request(method, url, data=JUNK_FORM if method == "POST" else None, follow_redirects=False)
        if resp.status_code >= 500:
            crashes.append(f"{method} {url} -> {resp.status_code}")
        if who and path == "/logout":
            login(client, who)  # logout was fuzzed too; stay logged in
    assert crashes == []


def test_anonymous_visitors_only_see_public_pages(people):
    client = TestClient(main_module.app, raise_server_exceptions=False)
    public = {"/", "/hilfe", "/login", "/signup", "/forgot-password", "/reset-password/{token}"}
    for method, path in routes():
        if method != "GET" or path in public:
            continue
        resp = client.get(fill(path, "1"), follow_redirects=False)
        assert resp.status_code in (303, 404) and resp.headers.get("location", "/login") == "/login", path


def test_unexpected_errors_show_a_german_page(people, monkeypatch):
    import routers.logbook as logbook

    def boom(*args, **kwargs):
        raise RuntimeError("database on fire")
    monkeypatch.setattr(logbook.templates, "TemplateResponse", boom)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    login(client, "pilot")
    resp = client.get("/logbook")
    assert resp.status_code == 500
    assert "Da ist etwas schiefgelaufen" in resp.text and "database on fire" not in resp.text
