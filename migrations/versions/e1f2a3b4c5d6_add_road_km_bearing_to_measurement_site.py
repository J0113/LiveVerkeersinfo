"""add_road_km_bearing_to_measurement_site

Revision ID: e1f2a3b4c5d6
Revises: c9e0f1a2b3c4
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'c9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('measurement_site', sa.Column('road', sa.String(), nullable=True))
    op.add_column('measurement_site', sa.Column('carriageway', sa.String(), nullable=True))
    op.add_column('measurement_site', sa.Column('km', sa.Numeric(), nullable=True))
    op.add_column('measurement_site', sa.Column('openlr_bearing', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('measurement_site', 'openlr_bearing')
    op.drop_column('measurement_site', 'km')
    op.drop_column('measurement_site', 'carriageway')
    op.drop_column('measurement_site', 'road')
