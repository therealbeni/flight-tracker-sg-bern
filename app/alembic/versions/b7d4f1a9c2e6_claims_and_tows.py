"""whole-day claims, releasing claims, aircraft kinds, tow links

Revision ID: b7d4f1a9c2e6
Revises: a3c1e7d2b9f4
Create Date: 2026-09-29 19:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d4f1a9c2e6'
down_revision: Union[str, None] = 'a3c1e7d2b9f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

aircraftkind = sa.Enum('GLIDER', 'MOTORGLIDER', 'TOWPLANE', name='aircraftkind')
launchmethod = sa.Enum('AEROTOW', 'WINCH', 'SELF', name='launchmethod')


def upgrade() -> None:
    aircraftkind.create(op.get_bind(), checkfirst=True)
    launchmethod.create(op.get_bind(), checkfirst=True)
    with op.batch_alter_table("gliders") as batch:
        batch.add_column(sa.Column("kind", aircraftkind, server_default="GLIDER", nullable=False))
    with op.batch_alter_table("glider_claims") as batch:
        batch.add_column(sa.Column("whole_day", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("flights") as batch:
        batch.add_column(sa.Column("launch_method", launchmethod, nullable=True))
        batch.add_column(sa.Column("tow_flight_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("flights_tow_flight_id_fkey", "flights", ["tow_flight_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch:
        batch.drop_constraint("flights_tow_flight_id_fkey", type_="foreignkey")
        batch.drop_column("tow_flight_id")
        batch.drop_column("launch_method")
    with op.batch_alter_table("glider_claims") as batch:
        batch.drop_column("cancelled_at")
        batch.drop_column("whole_day")
    with op.batch_alter_table("gliders") as batch:
        batch.drop_column("kind")
    launchmethod.drop(op.get_bind(), checkfirst=True)
    aircraftkind.drop(op.get_bind(), checkfirst=True)
