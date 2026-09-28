"""Re-exports the shared engine/session so existing `from database import X`
imports in this app keep working. Actual setup lives in shared/database.py,
which the tracker process also uses to write into the same schema."""

from shared.database import DATABASE_URL, Base, SessionLocal, engine, get_db  # noqa: F401
