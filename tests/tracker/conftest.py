"""Tracker tests: tracker/src (the tracker's modules) and the repo root (for the
`shared` package) on sys.path, and fresh tables per test (Postgres from dev/test.sh,
or SQLite)."""

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRACKER_SRC = REPO_ROOT / "tracker" / "src"
sys.path.insert(0, str(TRACKER_SRC))
sys.path.insert(0, str(REPO_ROOT))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db.name}")

import pytest

from shared.database import Base, engine, SessionLocal
import shared.models  # noqa: F401  (registers tables on Base.metadata)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()  # Postgres: pooled connections cache plans of the old tables
    yield


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
