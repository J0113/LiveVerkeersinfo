"""add explainable source-point to OSM road assignments

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a3
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "road_point_assignment",
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(), nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("source_kind", "source_key"),
    )
    op.create_index(
        "ix_road_point_assignment_status",
        "road_point_assignment",
        ["status"],
    )

    op.create_table(
        "road_point_link",
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("link_index", sa.Integer(), nullable=False),
        sa.Column("road_id", sa.BigInteger(), nullable=True),
        sa.Column("road_revision", sa.Integer(), nullable=True),
        sa.Column("segment_id", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("anchor_lane_id", sa.String(), nullable=True),
        sa.Column("applies_to_lane_id", sa.String(), nullable=True),
        sa.Column("position_fraction", sa.Numeric(), nullable=True),
        sa.Column(
            "matched_geom",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("source_distance_m", sa.Numeric(), nullable=True),
        sa.Column("bearing_error_deg", sa.Numeric(), nullable=True),
        sa.Column("road_ref_quality", sa.String(), nullable=True),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_kind", "source_key"],
            ["road_point_assignment.source_kind", "road_point_assignment.source_key"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_kind", "source_key", "link_index"),
    )
    op.create_index(
        "ix_road_point_link_segment_direction",
        "road_point_link",
        ["segment_id", "direction", "source_kind"],
    )
    op.create_index(
        "ix_road_point_link_road_revision_direction",
        "road_point_link",
        ["road_id", "road_revision", "direction", "source_kind"],
    )
    op.create_index(
        "ix_road_point_link_matched_geom",
        "road_point_link",
        ["matched_geom"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_road_point_link_source",
        "road_point_link",
        ["source_kind", "source_key"],
    )
    op.create_index("ix_road_point_link_anchor_lane", "road_point_link", ["anchor_lane_id"])
    op.create_index(
        "ix_road_point_link_applies_lane",
        "road_point_link",
        ["applies_to_lane_id"],
        postgresql_where=sa.text("applies_to_lane_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_road_point_link_applies_lane", table_name="road_point_link")
    op.drop_index("ix_road_point_link_anchor_lane", table_name="road_point_link")
    op.drop_index("ix_road_point_link_source", table_name="road_point_link")
    op.drop_index("ix_road_point_link_matched_geom", table_name="road_point_link")
    op.drop_index("ix_road_point_link_road_revision_direction", table_name="road_point_link")
    op.drop_index("ix_road_point_link_segment_direction", table_name="road_point_link")
    op.drop_table("road_point_link")
    op.drop_index("ix_road_point_assignment_status", table_name="road_point_assignment")
    op.drop_table("road_point_assignment")
