"""Add a geography index to osm_lane_centerline.

The speed-sensor matcher looks for lane geometry within a metric radius of a
site (``ST_DWithin(geom::geography, point::geography, 25)``). The plain GiST
index on ``geom`` cannot serve that cast, so without this functional index every
site falls back to a sequential scan of the whole national lane table — the same
index osm_road_lane has carried since the sensor match moved onto OSM geometry.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_osm_lane_centerline_geog",
        "osm_lane_centerline",
        [sa.text("(geom::geography)")],
        unique=False,
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_index("ix_osm_lane_centerline_geog", table_name="osm_lane_centerline")
