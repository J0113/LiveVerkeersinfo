"""Index feed run history lookups.

Revision ID: d1e2f3a4b5c6
Revises: b7c8d9e0f1a2
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_feed_run_feed_finished",
        "feed_run",
        ["feed", "finished_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feed_run_feed_finished", table_name="feed_run")
