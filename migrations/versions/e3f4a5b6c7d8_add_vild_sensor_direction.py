"""add VILD sensor direction enrichment and OSM geography index

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vild_tmc", sa.Column("hecto_dir", sa.Integer(), nullable=True))
    op.add_column("measurement_site", sa.Column("carriageway_source", sa.String(), nullable=True))
    op.add_column("measurement_site", sa.Column("vild_carriageway", sa.String(), nullable=True))
    op.add_column(
        "measurement_site",
        sa.Column("vild_carriageway_source", sa.String(), nullable=True),
    )
    op.add_column(
        "measurement_site",
        sa.Column("carriageway_direction_conflict", sa.Boolean(), nullable=True),
    )
    op.add_column("measurement_site", sa.Column("vild_bearing", sa.Numeric(), nullable=True))
    op.create_index(
        "ix_osm_road_lane_geog",
        "osm_road_lane",
        [sa.text("(geom::geography)")],
        unique=False,
        postgresql_using="gist",
    )

    # Values created solely by the old direction→R/L shortcut must not survive
    # until the next forced reference refresh. Explicit codes are repopulated by
    # the parser/ingester during rollout.
    op.execute(
        """
        UPDATE measurement_site
        SET carriageway = NULL
        WHERE id LIKE 'GEO%'
           OR (
               id LIKE 'RWS01_%'
               AND name ~ '^\\d{4}[a-z]{3}\\d{4}[a-z]{2}$'
               AND substring(name from 5 for 3) NOT IN ('hrr', 'hrl')
           )
        """
    )
    # Make both complete reference feeds run unconditionally once after rollout.
    # Without their prior validators, the poller performs a 200 download and
    # immediately replaces pre-enrichment rows instead of accepting a 304.
    op.execute(
        "DELETE FROM feed_run WHERE feed IN ('measurement_site', 'vild_shapefile')"
    )


def downgrade() -> None:
    op.drop_index("ix_osm_road_lane_geog", table_name="osm_road_lane")
    op.drop_column("measurement_site", "vild_bearing")
    op.drop_column("measurement_site", "carriageway_direction_conflict")
    op.drop_column("measurement_site", "vild_carriageway")
    op.drop_column("measurement_site", "vild_carriageway_source")
    op.drop_column("measurement_site", "carriageway_source")
    op.drop_column("vild_tmc", "hecto_dir")
