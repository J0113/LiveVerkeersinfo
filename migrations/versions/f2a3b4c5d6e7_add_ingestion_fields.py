"""add ingestion fields (speed n_inputs/std_dev, traveltime quality, drip display)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('traffic_measurement', sa.Column('n_inputs', sa.Integer(), nullable=True))
    op.add_column('traffic_measurement', sa.Column('std_dev', sa.Numeric(), nullable=True))
    op.add_column('travel_time', sa.Column('quality', sa.String(), nullable=True))
    op.add_column('drip', sa.Column('num_display_areas', sa.Integer(), nullable=True))
    op.add_column('drip', sa.Column('display_text', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('drip', 'display_text')
    op.drop_column('drip', 'num_display_areas')
    op.drop_column('travel_time', 'quality')
    op.drop_column('traffic_measurement', 'std_dev')
    op.drop_column('traffic_measurement', 'n_inputs')
