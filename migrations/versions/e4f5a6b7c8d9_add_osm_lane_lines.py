"""add independent OSM lane centerlines, connections, and way node references

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-29 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "osm_road",
        sa.Column("node_refs", postgresql.ARRAY(sa.BigInteger()), nullable=True),
    )

    op.create_table(
        "osm_lane_centerline",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "road_id",
            sa.BigInteger(),
            sa.ForeignKey("osm_road.osm_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("segment_id", sa.String(), nullable=False),
        sa.Column("lane_nr", sa.Integer(), nullable=False),
        sa.Column("lane_count", sa.Integer(), nullable=False),
        sa.Column("physical_lane_index", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("offset_m", sa.Numeric(), nullable=False),
        sa.Column("count_source", sa.String(), nullable=False),
        sa.Column("oneway_source", sa.String()),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("LINESTRING", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_osm_lane_centerline_geom",
        "osm_lane_centerline",
        ["geom"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_osm_lane_centerline_road_id", "osm_lane_centerline", ["road_id"]
    )
    op.create_index(
        "uq_osm_lane_centerline_segment_direction_lane",
        "osm_lane_centerline",
        ["segment_id", "direction", "lane_nr"],
        unique=True,
    )

    op.create_table(
        "osm_lane_connection",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "from_lane_id",
            sa.String(),
            sa.ForeignKey("osm_lane_centerline.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_direction", sa.String(), nullable=False),
        sa.Column(
            "to_lane_id",
            sa.String(),
            sa.ForeignKey("osm_lane_centerline.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("to_direction", sa.String(), nullable=False),
        sa.Column("from_road_id", sa.BigInteger(), nullable=False),
        sa.Column("to_road_id", sa.BigInteger(), nullable=False),
        sa.Column("from_segment_id", sa.String(), nullable=False),
        sa.Column("to_segment_id", sa.String(), nullable=False),
        sa.Column("connection_type", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("LINESTRING", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_osm_lane_connection_geom",
        "osm_lane_connection",
        ["geom"],
        postgresql_using="gist",
    )
    for column in (
        "from_road_id",
        "to_road_id",
        "from_segment_id",
        "to_segment_id",
    ):
        op.create_index(
            f"ix_osm_lane_connection_{column}", "osm_lane_connection", [column]
        )


def downgrade() -> None:
    op.drop_table("osm_lane_connection")
    op.drop_table("osm_lane_centerline")
    op.drop_column("osm_road", "node_refs")
