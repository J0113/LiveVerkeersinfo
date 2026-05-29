"""situation_pk_record_id

Revision ID: 6e6bb1a16e1b
Revises: fb23bfc11eb5
Create Date: 2026-05-29 16:18:42.812328

"""
from typing import Sequence, Union

import geoalchemy2
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e6bb1a16e1b'
down_revision: Union[str, None] = 'fb23bfc11eb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The original schema used situation.id as PK.
    # Parsers yield one row per situationRecord, so record_id must be the PK.
    # Truncate stale data (accumulated from failed ingest attempts) and swap PK.
    op.execute("TRUNCATE TABLE situation")
    op.drop_constraint("situation_pkey", "situation", type_="primary")
    op.alter_column("situation", "record_id", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("situation", "id", existing_type=sa.VARCHAR(), nullable=True)
    op.create_primary_key("situation_pkey", "situation", ["record_id"])
    op.create_index("ix_situation_id", "situation", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_situation_id", table_name="situation")
    op.drop_constraint("situation_pkey", "situation", type_="primary")
    op.alter_column("situation", "id", existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column("situation", "record_id", existing_type=sa.VARCHAR(), nullable=True)
    op.create_primary_key("situation_pkey", "situation", ["id"])
