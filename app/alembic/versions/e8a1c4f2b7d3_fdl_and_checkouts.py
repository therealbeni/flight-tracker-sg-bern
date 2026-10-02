"""Flugdienstleiter (FDL) role replaces Startstelle; checkouts are recorded

Revision ID: e8a1c4f2b7d3
Revises: d2f6b8e4a1c9
Create Date: 2026-10-02 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8a1c4f2b7d3'
down_revision: Union[str, None] = 'd2f6b8e4a1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE pilotrole RENAME VALUE 'FLIGHTDESK' TO 'FDL'")
    op.create_table(
        "checkouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pilot_id", sa.Integer(), sa.ForeignKey("pilots.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("checked_out_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by_pilot_id", sa.Integer(), sa.ForeignKey("pilots.id"), nullable=False),
        sa.UniqueConstraint("pilot_id", "day", name="checkouts_pilot_day_key"),
    )


def downgrade() -> None:
    op.drop_table("checkouts")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE pilotrole RENAME VALUE 'FDL' TO 'FLIGHTDESK'")
