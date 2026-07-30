"""Drop osm_road_lane.

The independent lane-line graph (osm_lane_centerline + osm_lane_connection)
replaced it everywhere: the map layer, the current-road HUD, and the speed
sensor match. Nothing reads this table any more, and it is derived data that a
PBF ingest rebuilds from scratch, so it is dropped rather than kept in step.

Revision ID: b7c8d9e0f1a3
Revises: a6b7c8d9e0f1
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a3"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_osm_road_lane_geog", table_name="osm_road_lane")
    op.drop_index("ix_osm_road_lane_geom", table_name="osm_road_lane")
    op.drop_index("ix_osm_road_lane_source_id", table_name="osm_road_lane")
    op.drop_table("osm_road_lane")


def downgrade() -> None:
    """Recreate the empty table. Rows only come back from a PBF re-ingest."""
    op.create_table(
        "osm_road_lane",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("lane_count", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("highway", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("width_m", sa.Numeric(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("raw", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["osm_road.osm_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_osm_road_lane_geom", "osm_road_lane", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_osm_road_lane_source_id", "osm_road_lane", ["source_id"])
    op.create_index(
        "ix_osm_road_lane_geog",
        "osm_road_lane",
        [sa.text("(geom::geography)")],
        postgresql_using="gist",
    )
