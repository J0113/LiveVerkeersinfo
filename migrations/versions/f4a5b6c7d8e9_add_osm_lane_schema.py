"""add compact OSM lane schema to directed road segments

Revision ID: f4a5b6c7d8e9
Revises: e2f3a4b5c6d7
Create Date: 2026-07-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing active graphs intentionally remain NULL. The roads API builds
    # the exact same fail-closed schema from their retained raw tags; the next
    # immutable graph import persists the schema directly.
    op.add_column(
        "osm_road_segment",
        sa.Column("lane_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("osm_road_segment", "lane_schema")
