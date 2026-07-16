"""add osm_road and osm_road_extract

Revision ID: c1d2e3f4a5b6
Revises: d1e2f3a4b5c6
Create Date: 2026-07-16 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "osm_road",
        sa.Column("osm_id", sa.BigInteger(), nullable=False),
        sa.Column("highway", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("osm_id"),
    )
    op.create_index("ix_osm_road_geom", "osm_road", ["geom"], unique=False, postgresql_using="gist")

    op.create_table(
        "osm_road_extract",
        sa.Column("extract_key", sa.String(), nullable=False),
        sa.Column("osm_id", sa.BigInteger(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["osm_id"], ["osm_road.osm_id"]),
        sa.PrimaryKeyConstraint("extract_key", "osm_id"),
    )


def downgrade() -> None:
    op.drop_table("osm_road_extract")
    op.drop_index("ix_osm_road_geom", table_name="osm_road", postgresql_using="gist")
    op.drop_table("osm_road")
