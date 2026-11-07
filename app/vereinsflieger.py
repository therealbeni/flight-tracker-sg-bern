"""Client for the club's Vereinsflieger (REST interface, see docs/vereinsflieger.md).

Vereinsflieger is the club's real system: flights there are billed to the
members. So this client is careful:

- Nothing talks to Vereinsflieger unless VEREINSFLIEGER_MODE says so:
  "off" (default) no calls at all, "readonly" only reading calls, "live" also
  writing ones. Checked here, for every call - not only in the UI.
- Every call is written to `vereinsflieger_calls` (without the accesstoken,
  AppKey or password), and counted: the AppKey allows 500 calls a day for
  everything using it, we stop at DAILY_BUDGET.
- Writing calls are never retried: if the answer is lost, the flight may well
  have been created, and a retry would create it twice.
"""

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import VereinsfliegerCall
from timeutil import local_day_bounds, to_local, today_local

BASE_URL = "https://www.vereinsflieger.de/interface/rest/"
MODES = ("off", "readonly", "live")
# Of the AppKey's 500 calls a day; the rest is left for whatever else uses the key.
DAILY_BUDGET = 400
TIMEOUT_S = 20
# Never stored in the call log.
SECRET_FIELDS = {"accesstoken", "appkey", "password", "auth_secret"}


class VereinsfliegerError(Exception):
    """A call failed or wasn't allowed. The message is German, for the UI."""


@dataclass
class Config:
    mode: str = "off"
    appkey: str = ""
    username: str = ""
    password: str = ""
    cid: str = ""  # only needed if the user is in several clubs
    auth_secret: str = ""  # two-factor authentication, if the user has it

    @classmethod
    def from_env(cls) -> "Config":
        mode = os.environ.get("VEREINSFLIEGER_MODE", "off").strip().lower()
        return cls(mode=mode if mode in MODES else "off",
                   appkey=os.environ.get("VEREINSFLIEGER_APPKEY", ""),
                   username=os.environ.get("VEREINSFLIEGER_USER", ""),
                   password=os.environ.get("VEREINSFLIEGER_PASSWORD", ""),
                   cid=os.environ.get("VEREINSFLIEGER_CID", ""),
                   auth_secret=os.environ.get("VEREINSFLIEGER_AUTH_SECRET", ""))

    @property
    def can_read(self) -> bool:
        return self.mode in ("readonly", "live") and bool(self.appkey and self.username and self.password)

    @property
    def can_write(self) -> bool:
        return self.mode == "live" and self.can_read


@dataclass
class Response:
    status: Optional[int]  # None: no answer
    data: dict


# (method, url, form fields) -> Response. Replaced by a fake in the tests.
Transport = Callable[[str, str, dict], Response]


def http_transport(method: str, url: str, fields: dict) -> Response:
    body = urllib.parse.urlencode(fields).encode() if fields else None
    request = urllib.request.Request(url, data=body, method=method)
    if body:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as answer:
            status, raw = answer.status, answer.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return Response(status, data if isinstance(data, dict) else {})


def password_hash(password: str) -> str:
    """md5 of the password, as Vereinsflieger wants it (their PHP client
    converts it to ISO-8859-1 first - matters for umlauts)."""
    return hashlib.md5(password.encode("iso-8859-1", errors="replace")).hexdigest()


def calls_today(db: Session) -> int:
    start, end = local_day_bounds(today_local())
    return db.scalar(select(func.count()).select_from(VereinsfliegerCall)
                     .where(VereinsfliegerCall.called_at >= start, VereinsfliegerCall.called_at < end)) or 0


class Client:
    """One logged-in session: use `with session(db) as vf: ...`."""

    def __init__(self, db: Session, config: Config, transport: Optional[Transport] = None,
                 by_pilot_id: Optional[int] = None):
        self.db = db
        self.config = config
        self.transport = transport or http_transport  # looked up now: the tests replace it
        self.by_pilot_id = by_pilot_id
        self.accesstoken: Optional[str] = None

    # -- the one way out ---------------------------------------------------

    def call(self, method: str, endpoint: str, fields: Optional[dict] = None, write: bool = False,
             flight_id: Optional[int] = None) -> Response:
        if not self.config.can_read:
            raise VereinsfliegerError("Die Verbindung zum Vereinsflieger ist ausgeschaltet.")
        if write and not self.config.can_write:
            raise VereinsfliegerError("Übertragen an den Vereinsflieger ist ausgeschaltet.")
        if calls_today(self.db) >= DAILY_BUDGET:
            raise VereinsfliegerError("Für heute sind keine Anfragen an den Vereinsflieger mehr übrig.")
        fields = dict(fields or {})
        if self.accesstoken and endpoint != "auth/accesstoken":
            fields.setdefault("accesstoken", self.accesstoken)
        log = VereinsfliegerCall(method=method, endpoint=endpoint.split("/" + self.accesstoken)[0]
                                 if self.accesstoken else endpoint,
                                 request=json.dumps({k: v for k, v in fields.items() if k not in SECRET_FIELDS},
                                                    ensure_ascii=False) or None,
                                 flight_id=flight_id, by_pilot_id=self.by_pilot_id)
        # Logged (and counted) before it's sent: a call that never comes back still counts.
        self.db.add(log)
        self.db.commit()
        try:
            answer = self.transport(method, BASE_URL + endpoint, fields)
        except (OSError, urllib.error.URLError) as e:
            log.error = f"keine Antwort: {e}"
            self.db.commit()
            raise VereinsfliegerError("Der Vereinsflieger antwortet nicht.") from e
        log.status = answer.status
        if answer.status != 200:
            log.error = str(answer.data.get("error") or answer.data)[:2000]
        self.db.commit()
        return answer

    def expect_ok(self, answer: Response, what: str) -> dict:
        if answer.status != 200:
            detail = answer.data.get("error") or f"HTTP {answer.status}"
            raise VereinsfliegerError(f"{what} fehlgeschlagen: {detail}")
        return answer.data

    # -- login -------------------------------------------------------------

    def sign_in(self) -> None:
        data = self.expect_ok(self.call("GET", "auth/accesstoken"), "Anmeldung")
        self.accesstoken = data.get("accesstoken")
        if not self.accesstoken:
            raise VereinsfliegerError("Anmeldung fehlgeschlagen: kein Accesstoken.")
        fields = {"username": self.config.username, "password": password_hash(self.config.password),
                  "appkey": self.config.appkey}
        if self.config.cid:
            fields["cid"] = self.config.cid
        if self.config.auth_secret:
            fields["auth_secret"] = self.config.auth_secret
        answer = self.call("POST", "auth/signin", fields)
        if answer.status != 200:
            if answer.data.get("need_2fa"):
                raise VereinsfliegerError("Anmeldung fehlgeschlagen: der Benutzer hat eine Zwei-Faktor-Anmeldung.")
            self.expect_ok(answer, "Anmeldung")

    def sign_out(self) -> None:
        if self.accesstoken:
            try:
                self.call("DELETE", f"auth/signout/{self.accesstoken}")
            finally:
                self.accesstoken = None

    # -- reading -----------------------------------------------------------

    def user(self) -> dict:
        return self.expect_ok(self.call("POST", "auth/getuser"), "Benutzer lesen")

    def flights_between(self, first: date, last: date) -> list[dict]:
        """All flights in Vereinsflieger from `first` to `last` (inclusive),
        including ones marked deleted (field `deleted`)."""
        data = self.expect_ok(self.call("POST", "flight/list/daterange",
                                        {"datefrom": first.isoformat(), "dateto": last.isoformat()}), "Flüge lesen")
        return flight_list(data)

    def flights_of_day(self, day: date) -> list[dict]:
        data = self.expect_ok(self.call("POST", "flight/list/date", {"dateparam": day.isoformat()}), "Flüge lesen")
        return flight_list(data)

    def flight(self, flid: int) -> dict:
        return self.expect_ok(self.call("POST", f"flight/get/{int(flid)}"), "Flug lesen")


    # -- writing (mode "live" only, never retried) ---------------------------

    def add_flight(self, fields: dict, flight_id: Optional[int] = None) -> dict:
        """Creates a flight; returns Vereinsflieger's data of it (with `flid`)."""
        return self.expect_ok(self.call("POST", "flight/add", fields, write=True, flight_id=flight_id),
                              "Flug übertragen")

    def delete_flight(self, flid: int, flight_id: Optional[int] = None) -> None:
        self.expect_ok(self.call("DELETE", f"flight/delete/{int(flid)}", write=True, flight_id=flight_id),
                       "Flug löschen")


def flight_list(data: dict) -> list[dict]:
    """A list answer is an object with the flights under "0", "1", ... next to
    "httpstatuscode"."""
    return [v for k, v in sorted(data.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else -1)
            if k.isdigit() and isinstance(v, dict)]


@contextmanager
def session(db: Session, config: Optional[Config] = None, transport: Optional[Transport] = None,
            by_pilot_id: Optional[int] = None):
    """Logged in for the `with` block, logged out again afterwards."""
    client = Client(db, config or Config.from_env(), transport, by_pilot_id)
    client.sign_in()
    try:
        yield client
    finally:
        client.sign_out()


def vf_time(dt: datetime) -> str:
    """A time as the club's Vereinsflieger takes it: 'YYYY-mm-dd HH:ii' in
    Swiss local time. (The PDF says UTC, but VF stores what it gets, and shows
    and bills in local time - tested 2026-11-07, docs/vereinsflieger.md.)"""
    return to_local(dt).strftime("%Y-%m-%d %H:%M")
