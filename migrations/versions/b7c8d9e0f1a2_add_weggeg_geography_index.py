"""add WEGGEG geography GiST index for spatial fallback matching

The speed map's WEGGEG geometry fallback (_attach_weggeg_matches) filters with
ST_DWithin(geom::geography, point::geography, radius). The geography cast made
the plain geometry GiST index (ix_weggeg_lane_geom) unusable, so the planner
fell back to a bitmap scan of every lane=1 row (~9700) and ran ST_LineMerge +
distance on each — ~730ms per feature, seconds per request. An expression index
on (geom::geography) lets ST_DWithin narrow to a handful of candidates.

Revision ID: b7c8d9e0f1a2
Revises: 96a6811a4a0a
Create Date: 2026-07-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "96a6811a4a0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_weggeg_lane_geog "
        "ON weggeg_lane USING gist ((geom::geography))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_weggeg_lane_geog")
