"""add_bearing_to_msi_sign

Revision ID: b7d8e9f01234
Revises: a1c2e3f4d5b6
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7d8e9f01234'
down_revision: Union[str, None] = 'a1c2e3f4d5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('msi_sign', sa.Column('bearing', sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column('msi_sign', 'bearing')
