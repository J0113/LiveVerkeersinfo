"""retire WEGGEG and NWB production tables

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-07-20 00:00:00.000000

Downgrade recreates empty legacy tables. Their data came from external bulk
feeds and therefore requires re-ingestion after a downgrade.
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("weggeg_lane")
    op.drop_table("nwb_road_segment")


def downgrade() -> None:
    op.create_table(
        "nwb_road_segment",
        sa.Column("wvk_id", sa.BigInteger(), primary_key=True),
        sa.Column("begin_junction_id", sa.BigInteger()),
        sa.Column("end_junction_id", sa.BigInteger()),
        sa.Column("road_number", sa.String()),
        sa.Column("street_name", sa.String()),
        sa.Column("road_manager_type", sa.String()),
        sa.Column("road_manager_name", sa.String()),
        sa.Column("direction", sa.String()),
        sa.Column("administrative_direction", sa.String()),
        sa.Column("carriageway_position", sa.String()),
        sa.Column("position_to_orientation_line", sa.String()),
        sa.Column("carriageway_type", sa.String()),
        sa.Column("frc", sa.Integer()),
        sa.Column("form_of_way", sa.Integer()),
        sa.Column("openlr", sa.String()),
        sa.Column("begin_km", sa.Numeric()),
        sa.Column("end_km", sa.Numeric()),
        sa.Column("length_m", sa.Numeric()),
        sa.Column("valid_from", sa.Date()),
        sa.Column("status", sa.String()),
        sa.Column("road_class", sa.String()),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("LINESTRING", srid=4326, spatial_index=False),
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
        "ix_nwb_road_segment_geom",
        "nwb_road_segment",
        ["geom"],
        postgresql_using="gist",
    )

    op.create_table(
        "weggeg_lane",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("lane", sa.Integer(), nullable=False),
        sa.Column("lane_count", sa.Integer(), nullable=False),
        sa.Column("road_number", sa.String()),
        sa.Column("direction", sa.String()),
        sa.Column("carriageway_side", sa.String()),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, spatial_index=False),
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
        "ix_weggeg_lane_geom", "weggeg_lane", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_weggeg_lane_source_id", "weggeg_lane", ["source_id"])
    op.create_index(
        "ix_weggeg_lane_road_side_lane",
        "weggeg_lane",
        ["road_number", "carriageway_side", "lane"],
    )
    op.create_index(
        "ix_weggeg_lane_geog",
        "weggeg_lane",
        [sa.text("(geom::geography)")],
        postgresql_using="gist",
    )
