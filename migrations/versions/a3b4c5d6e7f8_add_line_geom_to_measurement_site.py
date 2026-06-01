"""add line_geom (LineString) to measurement_site for travel-time segments

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-01 00:00:00.000000

"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'measurement_site',
        sa.Column(
            'line_geom',
            geoalchemy2.types.Geometry(
                geometry_type='LINESTRING', srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('measurement_site', 'line_geom')
