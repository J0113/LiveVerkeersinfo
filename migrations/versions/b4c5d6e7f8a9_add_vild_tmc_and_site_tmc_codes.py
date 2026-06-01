"""add vild_tmc table + measurement_site TMC codes (road-following travel time)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('measurement_site', sa.Column('tmc_primary', sa.Integer(), nullable=True))
    op.add_column('measurement_site', sa.Column('tmc_secondary', sa.Integer(), nullable=True))
    op.add_column('measurement_site', sa.Column('tmc_direction', sa.String(), nullable=True))

    op.create_table(
        'vild_tmc',
        sa.Column('loc_nr', sa.Integer(), nullable=False),
        sa.Column('lin_ref', sa.Integer(), nullable=True),
        sa.Column('pos_off', sa.Integer(), nullable=True),
        sa.Column('neg_off', sa.Integer(), nullable=True),
        sa.Column('road_number', sa.String(), nullable=True),
        sa.Column('raw', postgresql.JSONB(), nullable=True),
        sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('loc_nr'),
    )


def downgrade() -> None:
    op.drop_table('vild_tmc')
    op.drop_column('measurement_site', 'tmc_direction')
    op.drop_column('measurement_site', 'tmc_secondary')
    op.drop_column('measurement_site', 'tmc_primary')
