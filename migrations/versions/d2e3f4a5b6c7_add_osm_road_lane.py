"""add osm_road_lane

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-16 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "osm_road_lane",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("lane_count", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("highway", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("width_m", sa.Numeric(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["osm_road.osm_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_osm_road_lane_geom", "osm_road_lane", ["geom"], unique=False, postgresql_using="gist")
    op.create_index("ix_osm_road_lane_source_id", "osm_road_lane", ["source_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_osm_road_lane_source_id", table_name="osm_road_lane")
    op.drop_index("ix_osm_road_lane_geom", table_name="osm_road_lane", postgresql_using="gist")
    op.drop_table("osm_road_lane")
