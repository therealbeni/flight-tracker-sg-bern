"""Runs in a separate pytest process from tests/test_auth.py and tests/test_claim.py.

tracker/src/models.py and app/models.py are both importable as the bare name
`models` - each is fine on its own (that's how the two Docker containers run in
production, each with only one of them on PYTHONPATH), but mixing both on
sys.path in the same interpreter makes whichever loads first shadow the other.
So this conftest puts only tracker/src (+ the repo root, for the `shared`
package) on sys.path, and never touches app/.
"""

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
    yield


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
