"""add versioned OSM directed road graph and source bindings

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-15 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "osm_import_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("graph_version", sa.String(length=96), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("node_count", sa.BigInteger(), nullable=False),
        sa.Column("segment_count", sa.BigInteger(), nullable=False),
        sa.Column("way_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('importing', 'ready', 'active', 'superseded', 'failed')",
            name="ck_osm_import_run_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("graph_version"),
    )
    op.create_index(
        "uq_osm_import_run_one_active",
        "osm_import_run",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "osm_road_node",
        sa.Column("import_run_id", sa.BigInteger(), nullable=False),
        sa.Column("internal_node_id", sa.String(length=64), nullable=False),
        sa.Column("graph_version", sa.String(length=96), nullable=False),
        sa.Column("osm_node_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["import_run_id"], ["osm_import_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("import_run_id", "internal_node_id"),
        sa.UniqueConstraint(
            "graph_version", "internal_node_id", name="uq_osm_road_node_version_id"
        ),
    )
    op.create_index("ix_osm_road_node_osm_id", "osm_road_node", ["osm_node_id"])
    op.create_index(
        "ix_osm_road_node_geom", "osm_road_node", ["geom"], postgresql_using="gist"
    )

    op.create_table(
        "osm_road_segment",
        sa.Column("import_run_id", sa.BigInteger(), nullable=False),
        sa.Column("internal_segment_id", sa.String(length=64), nullable=False),
        sa.Column("graph_version", sa.String(length=96), nullable=False),
        sa.Column("osm_way_id", sa.BigInteger(), nullable=False),
        sa.Column("osm_version", sa.Integer(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_from_node_id", sa.BigInteger(), nullable=False),
        sa.Column("source_to_node_id", sa.BigInteger(), nullable=False),
        sa.Column("from_node_id", sa.String(length=64), nullable=False),
        sa.Column("to_node_id", sa.String(length=64), nullable=False),
        sa.Column("travel_direction", sa.String(length=12), nullable=False),
        sa.Column("highway", sa.String(length=32), nullable=False),
        sa.Column("road_number", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("oneway", sa.String(length=16), nullable=False),
        sa.Column("junction", sa.String(length=32), nullable=True),
        sa.Column("carriageway_ref", sa.String(length=64), nullable=True),
        sa.Column("lanes", sa.Integer(), nullable=True),
        sa.Column("maxspeed", sa.String(length=64), nullable=True),
        sa.Column("access", sa.String(length=32), nullable=True),
        sa.Column("bridge", sa.String(length=32), nullable=True),
        sa.Column("tunnel", sa.String(length=32), nullable=True),
        sa.Column("layer", sa.Integer(), nullable=True),
        sa.Column("length_m", sa.Numeric(), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="LINESTRING", srid=4326, spatial_index=False
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "travel_direction IN ('forward', 'backward', 'reverse')",
            name="ck_osm_road_segment_direction",
        ),
        sa.ForeignKeyConstraint(["import_run_id"], ["osm_import_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["import_run_id", "from_node_id"],
            ["osm_road_node.import_run_id", "osm_road_node.internal_node_id"],
            name="fk_osm_segment_from_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id", "to_node_id"],
            ["osm_road_node.import_run_id", "osm_road_node.internal_node_id"],
            name="fk_osm_segment_to_node",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("import_run_id", "internal_segment_id"),
        sa.UniqueConstraint(
            "graph_version", "internal_segment_id", name="uq_osm_road_segment_version_id"
        ),
    )
    op.create_index(
        "ix_osm_road_segment_geom", "osm_road_segment", ["geom"], postgresql_using="gist"
    )
    op.execute(
        "CREATE INDEX ix_osm_road_segment_geog "
        "ON osm_road_segment USING gist ((geom::geography))"
    )
    op.create_index("ix_osm_road_segment_way", "osm_road_segment", ["osm_way_id"])
    op.create_index(
        "ix_osm_road_segment_graph_from",
        "osm_road_segment",
        ["graph_version", "from_node_id"],
    )
    op.create_index(
        "ix_osm_road_segment_graph_to",
        "osm_road_segment",
        ["graph_version", "to_node_id"],
    )
    op.create_index(
        "ix_osm_road_segment_graph_ref",
        "osm_road_segment",
        ["graph_version", "road_number"],
    )

    op.create_table(
        "source_location_binding",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("internal_segment_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("distance_m", sa.Numeric(), nullable=True),
        sa.Column("heading_delta_deg", sa.Numeric(), nullable=True),
        sa.Column("score", sa.Numeric(), nullable=True),
        sa.Column("margin", sa.Numeric(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column("graph_version", sa.String(length=96), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'ambiguous', 'rejected')",
            name="ck_source_location_binding_status",
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND internal_segment_id IS NOT NULL) OR "
            "(status IN ('ambiguous', 'rejected') AND internal_segment_id IS NULL)",
            name="ck_source_binding_fail_closed_segment",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_source_binding_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["graph_version", "internal_segment_id"],
            ["osm_road_segment.graph_version", "osm_road_segment.internal_segment_id"],
            name="fk_source_binding_segment",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "graph_version",
            "algorithm_version",
            name="uq_source_location_binding_evaluation",
        ),
    )
    op.create_index(
        "ix_source_binding_status_segment",
        "source_location_binding",
        ["status", "internal_segment_id"],
    )
    op.create_index(
        "ix_source_binding_source",
        "source_location_binding",
        ["source_type", "source_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_binding_source", table_name="source_location_binding")
    op.drop_index("ix_source_binding_status_segment", table_name="source_location_binding")
    op.drop_table("source_location_binding")
    op.drop_index("ix_osm_road_segment_graph_ref", table_name="osm_road_segment")
    op.drop_index("ix_osm_road_segment_graph_to", table_name="osm_road_segment")
    op.drop_index("ix_osm_road_segment_graph_from", table_name="osm_road_segment")
    op.drop_index("ix_osm_road_segment_way", table_name="osm_road_segment")
    op.execute("DROP INDEX ix_osm_road_segment_geog")
    op.drop_index("ix_osm_road_segment_geom", table_name="osm_road_segment")
    op.drop_table("osm_road_segment")
    op.drop_index("ix_osm_road_node_geom", table_name="osm_road_node")
    op.drop_index("ix_osm_road_node_osm_id", table_name="osm_road_node")
    op.drop_table("osm_road_node")
    op.drop_index("uq_osm_import_run_one_active", table_name="osm_import_run")
    op.drop_table("osm_import_run")
