"""Flugart, Abrechnungsart, tow aircraft/pilot entered by hand

Revision ID: d2f6b8e4a1c9
Revises: c5e8a2d3f7b1
Create Date: 2026-09-30 08:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f6b8e4a1c9'
down_revision: Union[str, None] = 'c5e8a2d3f7b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch:
        batch.add_column(sa.Column("flight_type", sa.String(4), server_default="N", nullable=False))
        batch.add_column(sa.Column("billing", sa.String(24), server_default="pilot", nullable=False))
        batch.add_column(sa.Column("billing_member_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("tow_glider_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("tow_pilot_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("flights_billing_member_id_fkey", "pilots", ["billing_member_id"], ["id"])
        batch.create_foreign_key("flights_tow_glider_id_fkey", "gliders", ["tow_glider_id"], ["id"])
        batch.create_foreign_key("flights_tow_pilot_id_fkey", "pilots", ["tow_pilot_id"], ["id"])
    # Tow plane flights are "F-Schlepp" and not billed themselves (the towed glider is).
    op.execute("UPDATE flights SET flight_type = 'F', billing = 'none' WHERE glider_id IN "
               "(SELECT id FROM gliders WHERE kind = 'TOWPLANE')")


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch:
        for fk in ("flights_tow_pilot_id_fkey", "flights_tow_glider_id_fkey", "flights_billing_member_id_fkey"):
            batch.drop_constraint(fk, type_="foreignkey")
        for column in ("tow_pilot_id", "tow_glider_id", "billing_member_id", "billing", "flight_type"):
            batch.drop_column(column)
