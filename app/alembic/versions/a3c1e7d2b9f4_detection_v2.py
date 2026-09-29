"""detection v2: estimated takeoff/landing flags, longer airfield codes

Revision ID: a3c1e7d2b9f4
Revises: 557f8300ece0
Create Date: 2026-09-29 16:00:00

Airfield codes were limited to 4 characters, but fields without an ICAO code
are identified by their OurAirports ident (e.g. "CH-0012"). The tracker now
adds airfields it lands at automatically, so the column must fit those.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3c1e7d2b9f4'
down_revision: Union[str, None] = '557f8300ece0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ICAO_COLUMNS = [("airfields", "icao"), ("flights", "takeoff_airfield_icao"), ("flights", "landing_airfield_icao")]


def upgrade() -> None:
    for table, column in ICAO_COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=sa.String(16), existing_type=sa.String(4))
    with op.batch_alter_table("flights") as batch:
        batch.add_column(sa.Column("takeoff_estimated", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("landing_estimated", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch:
        batch.drop_column("landing_estimated")
        batch.drop_column("takeoff_estimated")
    for table, column in ICAO_COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=sa.String(4), existing_type=sa.String(16))
