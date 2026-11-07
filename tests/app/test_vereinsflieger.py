"""The Vereinsflieger client against a fake Vereinsflieger: modes, call log,
budget, login. No test reaches the real one (conftest)."""

import json
from datetime import date

import pytest

import vereinsflieger as vf
from models import VereinsfliegerCall


class FakeVereinsflieger:
    def __init__(self, signin_status=200, signin_data=None):
        self.requests = []
        self.signin_status = signin_status
        self.signin_data = signin_data or {}

    def __call__(self, method, url, fields):
        endpoint = url.removeprefix(vf.BASE_URL)
        self.requests.append((method, endpoint, dict(fields)))
        if endpoint == "auth/accesstoken":
            return vf.Response(200, {"httpstatuscode": 200, "accesstoken": "TOKEN123"})
        if endpoint == "auth/signin":
            return vf.Response(self.signin_status, self.signin_data)
        if endpoint == "flight/list/daterange":
            return vf.Response(200, {"0": {"flid": "11", "callsign": "HB-1"}, "1": {"flid": "12", "callsign": "HB-2"},
                                     "httpstatuscode": 200})
        return vf.Response(200, {"httpstatuscode": 200})


def config(mode="readonly"):
    return vf.Config(mode=mode, appkey="KEY", username="fdl@example.org", password="geheim")


def test_off_makes_no_call(db_session):
    fake = FakeVereinsflieger()
    with pytest.raises(vf.VereinsfliegerError, match="ausgeschaltet"):
        with vf.session(db_session, config("off"), fake):
            pass
    assert fake.requests == []


def test_missing_credentials_count_as_off(db_session):
    fake = FakeVereinsflieger()
    with pytest.raises(vf.VereinsfliegerError):
        with vf.session(db_session, vf.Config(mode="live", appkey="KEY"), fake):
            pass
    assert fake.requests == []


def test_unknown_mode_from_env_is_off(monkeypatch):
    monkeypatch.setenv("VEREINSFLIEGER_MODE", "LIVE!")
    assert vf.Config.from_env().mode == "off"


def test_readonly_reads_but_never_writes(db_session):
    fake = FakeVereinsflieger()
    with vf.session(db_session, config("readonly"), fake) as client:
        assert [f["flid"] for f in client.flights_between(date(2026, 10, 3), date(2026, 10, 4))] == ["11", "12"]
        with pytest.raises(vf.VereinsfliegerError, match="Übertragen .* ausgeschaltet"):
            client.call("POST", "flight/add", {"callsign": "HB-1"}, write=True)
    assert all(endpoint != "flight/add" for _, endpoint, _ in fake.requests)


def test_login_sends_md5_password_and_logs_out(db_session):
    fake = FakeVereinsflieger()
    with vf.session(db_session, config(), fake):
        pass
    endpoints = [e for _, e, _ in fake.requests]
    assert endpoints == ["auth/accesstoken", "auth/signin", "auth/signout/TOKEN123"]
    signin = fake.requests[1][2]
    assert signin["password"] == vf.password_hash("geheim") != "geheim"
    assert signin["accesstoken"] == "TOKEN123" and signin["appkey"] == "KEY"


def test_password_hash_like_their_php_client():
    # md5 of the ISO-8859-1 bytes, as VereinsfliegerRestInterface.php does.
    import hashlib
    assert vf.password_hash("Flügel") == hashlib.md5("Flügel".encode("iso-8859-1")).hexdigest()


def test_call_log_has_no_secrets(db_session):
    fake = FakeVereinsflieger()
    with vf.session(db_session, config(), fake) as client:
        client.flights_of_day(date(2026, 10, 3))
    logged = db_session.query(VereinsfliegerCall).order_by(VereinsfliegerCall.id).all()
    assert [c.endpoint for c in logged] == ["auth/accesstoken", "auth/signin", "flight/list/date", "auth/signout"]
    text = " ".join(f"{c.endpoint} {c.request}" for c in logged)
    for secret in ("TOKEN123", "KEY", "geheim", vf.password_hash("geheim")):
        assert secret not in text
    assert json.loads(logged[2].request) == {"dateparam": "2026-10-03"}
    assert all(c.status == 200 for c in logged)


def test_failed_login_is_explained(db_session):
    fake = FakeVereinsflieger(signin_status=403, signin_data={"error": "Falsches Passwort"})
    with pytest.raises(vf.VereinsfliegerError, match="Falsches Passwort"):
        with vf.session(db_session, config(), fake):
            pass
    fake2 = FakeVereinsflieger(signin_status=403, signin_data={"error": "x", "need_2fa": 1})
    with pytest.raises(vf.VereinsfliegerError, match="Zwei-Faktor"):
        with vf.session(db_session, config(), fake2):
            pass


def test_daily_budget_stops_calls(db_session):
    for _ in range(vf.DAILY_BUDGET):
        db_session.add(VereinsfliegerCall(method="POST", endpoint="flight/list/date"))
    db_session.commit()
    fake = FakeVereinsflieger()
    with pytest.raises(vf.VereinsfliegerError, match="keine Anfragen"):
        with vf.session(db_session, config(), fake):
            pass
    assert fake.requests == []


def test_no_answer_is_logged_and_counted(db_session):
    def down(method, url, fields):
        raise OSError("timed out")
    with pytest.raises(vf.VereinsfliegerError, match="antwortet nicht"):
        with vf.session(db_session, config(), down):
            pass
    call = db_session.query(VereinsfliegerCall).one()
    assert call.status is None and "timed out" in call.error
    assert vf.calls_today(db_session) == 1


def test_the_tests_cannot_reach_the_real_one(db_session):
    with pytest.raises(AssertionError, match="real Vereinsflieger"):
        with vf.session(db_session, config()):
            pass
