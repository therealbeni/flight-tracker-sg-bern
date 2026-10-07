"""Flight tracks: positions while in the air, packed per flight at landing

Revision ID: a7c3e9f1d5b8
Revises: f3b9d5a7c1e2
Create Date: 2026-10-07 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e9f1d5b8'
down_revision: Union[str, None] = 'f3b9d5a7c1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "track_points",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("flight_id", sa.Integer(), sa.ForeignKey("flights.id", ondelete="CASCADE"), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("altitude_m", sa.Float(), nullable=False),
        sa.Column("ground_m", sa.Float(), nullable=True),
        sa.Column("speed_kmh", sa.Float(), nullable=False),
        sa.Column("climb_ms", sa.Float(), nullable=True),
    )
    op.create_index("ix_track_points_flight_time", "track_points", ["flight_id", "time"])
    op.create_table(
        "flight_tracks",
        sa.Column("flight_id", sa.Integer(), sa.ForeignKey("flights.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("last_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_latitude", sa.Float(), nullable=False),
        sa.Column("last_longitude", sa.Float(), nullable=False),
        sa.Column("last_altitude_m", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("flight_tracks")
    op.drop_index("ix_track_points_flight_time", "track_points")
    op.drop_table("track_points")
