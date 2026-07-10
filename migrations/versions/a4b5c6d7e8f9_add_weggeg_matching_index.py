"""add WEGGEG road/carriageway/lane matching index

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-10 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_weggeg_lane_road_side_lane",
        "weggeg_lane",
        ["road_number", "carriageway_side", "lane"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_weggeg_lane_road_side_lane", table_name="weggeg_lane")
