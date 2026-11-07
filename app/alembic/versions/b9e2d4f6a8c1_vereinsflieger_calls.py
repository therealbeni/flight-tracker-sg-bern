"""Log of the calls to the Vereinsflieger REST API

Revision ID: b9e2d4f6a8c1
Revises: a7c3e9f1d5b8
Create Date: 2026-11-07 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9e2d4f6a8c1'
down_revision: Union[str, None] = 'a7c3e9f1d5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vereinsflieger_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("endpoint", sa.String(128), nullable=False),
        sa.Column("request", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("flight_id", sa.Integer(), sa.ForeignKey("flights.id"), nullable=True),
        sa.Column("by_pilot_id", sa.Integer(), sa.ForeignKey("pilots.id"), nullable=True),
    )
    op.create_index("ix_vereinsflieger_calls_called_at", "vereinsflieger_calls", ["called_at"])


def downgrade() -> None:
    op.drop_index("ix_vereinsflieger_calls_called_at", table_name="vereinsflieger_calls")
    op.drop_table("vereinsflieger_calls")
