import os
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db.name}")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("BASE_URL", "http://testserver")

import pytest
from fastapi.testclient import TestClient

from database import Base, engine, SessionLocal
import main as main_module


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(main_module.app)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class FakeEmailSender:
    def __init__(self):
        self.messages = []

    def send(self, to, subject, body):
        self.messages.append({"to": to, "subject": subject, "body": body})


@pytest.fixture
def fake_email(monkeypatch):
    import email_sender

    fake = FakeEmailSender()
    monkeypatch.setattr(email_sender, "sender", fake)
    monkeypatch.setattr("routers.auth.sender", fake)
    return fake
