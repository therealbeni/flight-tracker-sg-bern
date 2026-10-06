"""Private aircraft (owners), who made a check-in, and the launch method of
gliders logged so far: winch where no tow plane took off with them

Revision ID: f3b9d5a7c1e2
Revises: e8a1c4f2b7d3
Create Date: 2026-10-06 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b9d5a7c1e2'
down_revision: Union[str, None] = 'e8a1c4f2b7d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aircraft_owners",
        sa.Column("glider_id", sa.Integer(), sa.ForeignKey("gliders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("pilot_id", sa.Integer(), sa.ForeignKey("pilots.id", ondelete="CASCADE"), primary_key=True),
    )
    with op.batch_alter_table("glider_claims") as batch:
        batch.add_column(sa.Column("claimed_by_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("glider_claims_claimed_by_id_fkey", "pilots", ["claimed_by_id"], ["id"])
    # The tracker now sets Winde when no tow plane took off with a glider
    # (tracker/src/db_sink.py). Same for the open flights it logged before.
    op.execute("""
        UPDATE flights SET launch_method = 'WINCH'
        WHERE launch_method IS NULL AND source = 'AUTO' AND tow_flight_id IS NULL AND deleted_at IS NULL
          AND finalized_at IS NULL AND takeoff_estimated = false AND takeoff_airfield_icao IS NOT NULL
          AND glider_id IN (SELECT id FROM gliders WHERE kind = 'GLIDER')
    """)


def downgrade() -> None:
    with op.batch_alter_table("glider_claims") as batch:
        batch.drop_constraint("glider_claims_claimed_by_id_fkey", type_="foreignkey")
        batch.drop_column("claimed_by_id")
    op.drop_table("aircraft_owners")
