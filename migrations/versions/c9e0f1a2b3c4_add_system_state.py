"""add_system_state

Revision ID: c9e0f1a2b3c4
Revises: b7d8e9f01234
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9e0f1a2b3c4'
down_revision: Union[str, None] = 'b7d8e9f01234'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'system_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('last_api_request_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('system_state')
