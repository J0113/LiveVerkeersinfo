"""merge nwb and weggeg branches

Revision ID: 96a6811a4a0a
Revises: 6fb1dfebfe79, a4b5c6d7e8f9
Create Date: 2026-07-10 15:27:38.568956

"""
from typing import Sequence, Union

import geoalchemy2
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96a6811a4a0a'
down_revision: Union[str, None] = ('6fb1dfebfe79', 'a4b5c6d7e8f9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
