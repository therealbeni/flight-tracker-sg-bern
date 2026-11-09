"""Members, aircraft and places seen in the club's Vereinsflieger; one pilot
per Vereinsflieger member

Revision ID: c4a7e1d9f3b2
Revises: b9e2d4f6a8c1
Create Date: 2026-11-09 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a7e1d9f3b2'
down_revision: Union[str, None] = 'b9e2d4f6a8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vereinsflieger_entries",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("flights", sa.Integer(), nullable=False),
        sa.Column("last_flight", sa.Date(), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("pilots") as batch:
        batch.create_unique_constraint("pilots_vereinsflieger_member_id_key", ["vereinsflieger_member_id"])


def downgrade() -> None:
    with op.batch_alter_table("pilots") as batch:
        batch.drop_constraint("pilots_vereinsflieger_member_id_key", type_="unique")
    op.drop_table("vereinsflieger_entries")
