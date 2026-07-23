"""add effective_road/effective_carriageway/effective_source to measurement_site

Materializes the road/carriageway resolution that the /traffic/speed endpoint
previously computed per-request (explicit -> VILD-derived -> co-located
inherit), so it can be indexed and queried directly instead of scanned via
bbox + Python merge. Populated by
ndwinfo.ingest.vild_direction.resolve_effective_road (run from the same hooks
as rebuild_speed_site_directions), not backfilled here.

Revision ID: d3e4f5a6b7c8
Revises: c7d8e9f0a1b2
Create Date: 2026-07-23 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("measurement_site", sa.Column("effective_road", sa.String(), nullable=True))
    op.add_column("measurement_site", sa.Column("effective_carriageway", sa.String(), nullable=True))
    op.add_column("measurement_site", sa.Column("effective_source", sa.String(), nullable=True))
    op.create_index(
        "ix_measurement_site_effective_road",
        "measurement_site",
        ["effective_road", "effective_carriageway", "km"],
    )


def downgrade() -> None:
    op.drop_index("ix_measurement_site_effective_road", table_name="measurement_site")
    op.drop_column("measurement_site", "effective_source")
    op.drop_column("measurement_site", "effective_carriageway")
    op.drop_column("measurement_site", "effective_road")
