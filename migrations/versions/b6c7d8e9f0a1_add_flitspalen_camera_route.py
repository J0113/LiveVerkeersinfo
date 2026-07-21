"""add flitspalen_camera_route table

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-07-21 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flitspalen_camera_route",
        sa.Column("sc_id", sa.BigInteger(), primary_key=True),
        sa.Column("sce_id", sa.BigInteger(), nullable=False),
        sa.Column("street", sa.String()),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("LINESTRING", srid=4326, spatial_index=False),
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_flitspalen_camera_route_geom",
        "flitspalen_camera_route",
        ["geom"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_table("flitspalen_camera_route")
