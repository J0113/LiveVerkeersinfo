"""preserve open-source location, quality and provenance fields

Revision ID: 0a1b2c3d4e5f
Revises: f4a5b6c7d8e9
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0a1b2c3d4e5f"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_location_binding",
        sa.Column("direction_source", sa.String(), nullable=True),
    )
    op.add_column(
        "osm_road_segment",
        sa.Column("maxspeed_conditional", sa.String(), nullable=True),
    )
    op.add_column("osm_road_segment", sa.Column("placement", sa.String(), nullable=True))
    op.add_column("osm_road_segment", sa.Column("shoulder", sa.String(), nullable=True))
    for name, column_type in (
        ("equipment_reference", sa.String()),
        ("computation_method", sa.String()),
        ("carriageway_type", sa.String()),
        ("carriageway_source", sa.String()),
        ("openlr_side_of_road", sa.String()),
        ("openlr_orientation", sa.String()),
        ("openlr_positive_offset_m", sa.Integer()),
        ("openlr_frc", sa.String()),
        ("openlr_fow", sa.String()),
        ("openlr_data", postgresql.JSONB(astext_type=sa.Text())),
        ("tmc_country_code", sa.String()),
        ("tmc_table_number", sa.String()),
        ("tmc_table_version", sa.String()),
        ("tmc_offset_m", sa.Integer()),
    ):
        op.add_column("measurement_site", sa.Column(name, column_type, nullable=True))

    # Existing carriageway values were produced by the retired
    # positive/negative -> R/L shortcut. VILD direction is not an R/L code, so
    # fail closed until the next MST ingest restores only explicitly encoded
    # HRL/HRR or Re/Li evidence with a carriageway_source provenance value.
    op.execute("UPDATE measurement_site SET carriageway=NULL, carriageway_source=NULL")

    op.add_column("measurement_characteristic", sa.Column("accuracy", sa.Numeric(), nullable=True))
    op.add_column("measurement_characteristic", sa.Column("vehicle_type", sa.String(), nullable=True))
    op.add_column("vild_tmc", sa.Column("country_code", sa.String(), nullable=True))
    op.add_column("vild_tmc", sa.Column("table_number", sa.String(), nullable=True))
    op.add_column("vild_tmc", sa.Column("table_version", sa.String(), nullable=True))
    op.add_column("vild_tmc", sa.Column("hecto_direction", sa.Integer(), nullable=True))
    # The installed/configured static table is VILD6.13.A for the Netherlands.
    # Future ingests overwrite these fields from the versioned archive name.
    op.execute(
        "UPDATE vild_tmc SET country_code='8', table_number='6.13', table_version='A'"
    )

    for name, column_type in (
        ("n_incomplete_inputs", sa.Integer()),
        ("supplier_quality", sa.Numeric()),
        ("computational_method", sa.String()),
        ("data_error", sa.Boolean()),
        ("measurement_status", sa.String()),
        ("is_usable", sa.Boolean()),
    ):
        op.add_column("traffic_measurement", sa.Column(name, column_type, nullable=True))

    # Legacy rows do not carry reliable publication provenance and were often
    # misclassified by feed-wide category. This is a small live snapshot that
    # refreshes every minute; retaining it would create permanent shadow
    # duplicates that no feed-owned prune can safely remove.
    op.execute("TRUNCATE TABLE situation")
    op.add_column("situation", sa.Column("feed_name", sa.String(), nullable=False))
    op.drop_constraint("situation_pkey", "situation", type_="primary")
    op.create_primary_key("situation_pkey", "situation", ["record_id", "feed_name"])
    op.create_index("ix_situation_record_id", "situation", ["record_id"], unique=False)
    for name, column_type in (
        ("record_subtype", sa.String()),
        ("record_version", sa.Integer()),
        ("carriageway", sa.String()),
        ("bearing", sa.Numeric()),
        ("alert_c", postgresql.JSONB(astext_type=sa.Text())),
        ("locations", postgresql.JSONB(astext_type=sa.Text())),
        ("lane_impact", postgresql.JSONB(astext_type=sa.Text())),
        ("operator_action_status", sa.String()),
        ("record_status", sa.String()),
        ("validity_status", sa.String()),
        ("validity", postgresql.JSONB(astext_type=sa.Text())),
        ("information_status", sa.String()),
        ("cause", postgresql.JSONB(astext_type=sa.Text())),
    ):
        op.add_column("situation", sa.Column(name, column_type, nullable=True))

    op.add_column("drip", sa.Column("carriageway", sa.String(), nullable=True))

    for name, column_type in (
        ("sequence_number", sa.Integer()),
        ("wol_lane_number", sa.Integer()),
        ("begin_wdl", sa.String()),
        ("begin_km", sa.Numeric()),
        ("end_wdl", sa.String()),
        ("end_km", sa.Numeric()),
    ):
        op.add_column("weggeg_lane", sa.Column(name, column_type, nullable=True))

    op.create_table(
        "weggeg_road_attribute",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("feature_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("subtype", sa.String(), nullable=True),
        sa.Column("road_number", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("carriageway_side", sa.String(), nullable=True),
        sa.Column("begin_wdl", sa.String(), nullable=True),
        sa.Column("begin_km", sa.Numeric(), nullable=True),
        sa.Column("end_wdl", sa.String(), nullable=True),
        sa.Column("end_km", sa.Numeric(), nullable=True),
        sa.Column("point_km", sa.Numeric(), nullable=True),
        sa.Column("maxspeed_kmh", sa.Integer(), nullable=True),
        sa.Column("begin_time", sa.Numeric(), nullable=True),
        sa.Column("end_time", sa.Numeric(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(
                geometry_type="GEOMETRY", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_weggeg_road_attribute_geom", "weggeg_road_attribute", ["geom"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_weggeg_road_attribute_lookup", "weggeg_road_attribute",
        ["feature_type", "road_number", "carriageway_side", "begin_km", "end_km"],
    )


def downgrade() -> None:
    op.drop_column("source_location_binding", "direction_source")
    op.drop_index("ix_weggeg_road_attribute_lookup", table_name="weggeg_road_attribute")
    op.drop_index(
        "ix_weggeg_road_attribute_geom", table_name="weggeg_road_attribute",
        postgresql_using="gist",
    )
    op.drop_table("weggeg_road_attribute")
    op.drop_column("osm_road_segment", "shoulder")
    op.drop_column("osm_road_segment", "placement")
    op.drop_column("osm_road_segment", "maxspeed_conditional")
    for name in ("end_km", "end_wdl", "begin_km", "begin_wdl", "wol_lane_number", "sequence_number"):
        op.drop_column("weggeg_lane", name)
    op.drop_column("drip", "carriageway")
    for name in (
        "cause", "information_status", "validity", "validity_status",
        "record_status", "operator_action_status", "lane_impact", "locations",
        "alert_c", "bearing", "carriageway", "record_version", "record_subtype",
    ):
        op.drop_column("situation", name)
    op.drop_index("ix_situation_record_id", table_name="situation")
    # Composite provenance permits the same record in multiple shadow feeds.
    # Retain the same deterministic canonical winner used by the API before
    # restoring the legacy record_id-only key.
    op.execute(
        """
        DELETE FROM situation
        WHERE ctid NOT IN (
            SELECT DISTINCT ON (record_id) ctid
            FROM situation
            ORDER BY record_id,
                     version_time DESC NULLS LAST,
                     record_version DESC NULLS LAST,
                     CASE WHEN feed_name = 'actueel_beeld' THEN 0 ELSE 1 END,
                     feed_name
        )
        """
    )
    op.drop_constraint("situation_pkey", "situation", type_="primary")
    op.create_primary_key("situation_pkey", "situation", ["record_id"])
    op.drop_column("situation", "feed_name")
    for name in (
        "is_usable", "measurement_status", "data_error", "computational_method",
        "supplier_quality", "n_incomplete_inputs",
    ):
        op.drop_column("traffic_measurement", name)
    op.drop_column("measurement_characteristic", "vehicle_type")
    op.drop_column("measurement_characteristic", "accuracy")
    op.drop_column("vild_tmc", "hecto_direction")
    op.drop_column("vild_tmc", "table_version")
    op.drop_column("vild_tmc", "table_number")
    op.drop_column("vild_tmc", "country_code")
    for name in (
        "tmc_offset_m", "tmc_table_version", "tmc_table_number", "tmc_country_code",
        "openlr_data", "openlr_fow", "openlr_frc", "openlr_positive_offset_m",
        "openlr_orientation", "openlr_side_of_road", "carriageway_source", "carriageway_type",
        "computation_method", "equipment_reference",
    ):
        op.drop_column("measurement_site", name)
