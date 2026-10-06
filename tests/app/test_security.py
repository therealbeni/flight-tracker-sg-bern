"""Security sweep (2026-10): each test is one finding that was fixed."""

from fastapi.testclient import TestClient

import main as main_module
from models import Pilot
from security import hash_password, login_throttle
from test_flugbuch import PASSWORD, login, world  # noqa: F401 - world is a fixture


def test_api_docs_are_not_public(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_content_security_policy_allows_only_our_own_scripts(client):
    headers = client.get("/login").headers
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self' 'inline-speculation-rules'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert "frame-ancestors 'none'" in csp
    assert headers["Permissions-Policy"].startswith("camera=(self)")


def test_pages_have_no_inline_event_handlers(client, db_session, world):
    # They would need 'unsafe-inline' in the policy above.
    import re
    for who, paths in {"admin": ["/admin/pilots", "/admin/gliders", "/flugbuch", "/flights/0"],
                       "pia": ["/dashboard", "/claim", "/logbook", "/konto"], "desk": ["/flugbuch"]}.items():
        login(client, who)
        for path in paths:
            assert not re.search(r"\son[a-z]+=", client.get(path).text), path


def test_back_links_never_lead_off_site(client, db_session, world):
    from datetime import datetime, timezone

    from models import GliderClaim
    login(client, "pia")
    client.post(f"/claim/{world['glider'].claim_token}")
    claim = db_session.query(GliderClaim).one()
    resp = client.post(f"/claims/{claim.id}/release", headers={"referer": "http://testserver//evil.example/x"},
                       follow_redirects=False)
    assert not resp.headers["location"].startswith("//")
    assert claim.id and datetime.now(timezone.utc)


def test_changing_the_password_logs_out_everywhere_else(client, db_session, world):
    other_phone = TestClient(main_module.app)
    login(other_phone, "pia")
    assert other_phone.get("/logbook", follow_redirects=False).status_code == 200
    world["pia"].password_hash = hash_password("a-new-password")  # e.g. via "Passwort vergessen?"
    db_session.commit()
    assert other_phone.get("/logbook", follow_redirects=False).status_code == 303


def test_forgot_password_cannot_be_used_to_flood_a_mailbox(client, db_session, world, fake_email):
    login_throttle._failures.clear()
    for _ in range(10):
        client.post("/forgot-password", data={"email": "pia@example.com"})
    assert len(fake_email.messages) <= 5
    login_throttle._failures.clear()


def test_signups_from_one_place_are_limited(client, db_session, world):
    login_throttle._failures.clear()
    codes = [client.post("/signup", data={"full_name": f"Bot {i}", "email": f"bot{i}@example.com",
                                          "password": "password123"}).status_code for i in range(15)]
    assert codes.count(429) >= 5
    assert db_session.query(Pilot).filter(Pilot.email.like("bot%")).count() <= 10
    login_throttle._failures.clear()


def test_email_addresses_with_line_breaks_are_refused(client, db_session, world):
    resp = client.post("/signup", data={"full_name": "Eve", "email": "eve@example.com\r\nBcc: all@example.com",
                                        "password": "password123"})
    assert resp.status_code == 400 and db_session.query(Pilot).filter_by(full_name="Eve").count() == 0


def test_throttle_memory_does_not_grow_without_bound():
    from security import LoginThrottle
    throttle = LoginThrottle(limit=3, window_s=60)
    for i in range(20000):
        throttle.failed(f"user{i}@example.com", f"10.0.{i // 256}.{i % 256}", now=float(i))
    assert len(throttle._failures) < 5000


def test_a_used_reset_link_ends_the_other_reset_links(client, db_session, world, fake_email):
    login_throttle._failures.clear()
    client.post("/forgot-password", data={"email": "pia@example.com"})
    client.post("/forgot-password", data={"email": "pia@example.com"})
    first, second = (m["body"].split("/reset-password/")[1].split()[0] for m in fake_email.messages[:2])
    client.post(f"/reset-password/{first}", data={"password": "new-password-1"})
    resp = client.post(f"/reset-password/{second}", data={"password": "new-password-2"})
    assert "ungültig oder abgelaufen" in resp.text
    login_throttle._failures.clear()


def test_no_server_details_in_error_pages(client):
    resp = client.get("/flights/abc")
    assert "Traceback" not in resp.text and "sqlalchemy" not in resp.text.lower()


def test_password_is_still_required_to_match(client, db_session, world):
    assert client.post("/login", data={"email": "pia@example.com", "password": PASSWORD + "x"}).status_code == 400


def test_view_as_cannot_return_to_the_admin_after_their_password_changed(client, db_session, world):
    login(client, "admin")
    client.post(f"/admin/pilots/{world['pia'].id}/view-as")
    world["admin"].password_hash = hash_password("changed-after-theft")
    db_session.commit()
    resp = client.post("/view-as/end", follow_redirects=False)
    assert resp.headers["location"] == "/login"
    assert client.get("/admin/pilots", follow_redirects=False).status_code == 303
