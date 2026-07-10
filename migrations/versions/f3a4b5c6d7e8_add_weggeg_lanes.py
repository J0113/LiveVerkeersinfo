"""add WEGGEG-derived lane geometry

Revision ID: f3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-07-10 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weggeg_lane",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("lane_count", sa.Integer(), nullable=False),
        sa.Column("road_number", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("carriageway_side", sa.String(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weggeg_lane_geom", "weggeg_lane", ["geom"], unique=False, postgresql_using="gist")
    op.create_index("ix_weggeg_lane_source_id", "weggeg_lane", ["source_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_weggeg_lane_source_id", table_name="weggeg_lane")
    op.drop_index("ix_weggeg_lane_geom", table_name="weggeg_lane", postgresql_using="gist")
    op.drop_table("weggeg_lane")
