"""Index lane foreign keys used by rebuild deletes.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_osm_lane_connection_from_lane_id",
        "osm_lane_connection",
        ["from_lane_id"],
    )
    op.create_index(
        "ix_osm_lane_connection_to_lane_id",
        "osm_lane_connection",
        ["to_lane_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_osm_lane_connection_to_lane_id",
        table_name="osm_lane_connection",
    )
    op.drop_index(
        "ix_osm_lane_connection_from_lane_id",
        table_name="osm_lane_connection",
    )
