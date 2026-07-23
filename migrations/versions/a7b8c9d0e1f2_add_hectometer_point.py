"""add hectometer_point

Revision ID: a7b8c9d0e1f2
Revises: c7d8e9f0a1b2
Create Date: 2026-07-23 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hectometer_point",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("road", sa.String(), nullable=True),
        sa.Column("carriageway", sa.String(), nullable=True),
        sa.Column("km", sa.Numeric(), nullable=True),
        sa.Column("matched_osm_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hectometer_point_geom", "hectometer_point", ["geom"], unique=False, postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("ix_hectometer_point_geom", table_name="hectometer_point", postgresql_using="gist")
    op.drop_table("hectometer_point")
