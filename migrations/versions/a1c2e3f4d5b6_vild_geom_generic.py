"""vild_line and vild_area geom columns: LINESTRING/POLYGON → GEOMETRY

Revision ID: a1c2e3f4d5b6
Revises: 0ebe7dd7eb91
Create Date: 2026-05-31

"""
from __future__ import annotations

from typing import Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = 'a1c2e3f4d5b6'
down_revision: Union[str, None] = '0ebe7dd7eb91'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE vild_line ALTER COLUMN geom TYPE geometry(GEOMETRY,4326) "
        "USING geom::geometry(GEOMETRY,4326)"
    )
    op.execute(
        "ALTER TABLE vild_area ALTER COLUMN geom TYPE geometry(GEOMETRY,4326) "
        "USING geom::geometry(GEOMETRY,4326)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE vild_line ALTER COLUMN geom TYPE geometry(LINESTRING,4326) "
        "USING geom::geometry(LINESTRING,4326)"
    )
    op.execute(
        "ALTER TABLE vild_area ALTER COLUMN geom TYPE geometry(POLYGON,4326) "
        "USING geom::geometry(POLYGON,4326)"
    )
