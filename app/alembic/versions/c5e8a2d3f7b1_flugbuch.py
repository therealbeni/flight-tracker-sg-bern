"""Flugbuch: club PC role, guest pilots, companions, landings, soft delete

Revision ID: c5e8a2d3f7b1
Revises: b7d4f1a9c2e6
Create Date: 2026-09-29 20:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5e8a2d3f7b1'
down_revision: Union[str, None] = 'b7d4f1a9c2e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE pilotrole ADD VALUE IF NOT EXISTS 'FLIGHTDESK'")
    with op.batch_alter_table("flights") as batch:
        batch.add_column(sa.Column("pilot_name", sa.String(255), nullable=True))
        batch.add_column(sa.Column("companion_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("companion_name", sa.String(255), nullable=True))
        batch.add_column(sa.Column("landings", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("flights_companion_id_fkey", "pilots", ["companion_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch:
        batch.drop_constraint("flights_companion_id_fkey", type_="foreignkey")
        batch.drop_column("deleted_at")
        batch.drop_column("landings")
        batch.drop_column("companion_name")
        batch.drop_column("companion_id")
        batch.drop_column("pilot_name")
    # Postgres can't drop a value from an enum type; FLIGHTDESK stays defined.
