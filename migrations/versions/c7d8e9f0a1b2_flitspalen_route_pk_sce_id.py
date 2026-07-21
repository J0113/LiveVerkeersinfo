"""flitspalen_camera_route: primary key sc_id -> sce_id

sc_id is not one-to-one (an entry gantry can have multiple exit-lane SCE
cameras that all resolve to the same sc_id via paired_sc_id()'s floor
division), so it can't be a primary key -- two such rows in the same ingest
batch made bulk_upsert's ON CONFLICT (sc_id) hit the same row twice in one
statement, which Postgres rejects (CardinalityViolation). sce_id is a real
camera id and always unique.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-07-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("TRUNCATE flitspalen_camera_route")  # fully derived data, regenerated on next ingest
    op.drop_constraint("flitspalen_camera_route_pkey", "flitspalen_camera_route", type_="primary")
    op.create_primary_key("flitspalen_camera_route_pkey", "flitspalen_camera_route", ["sce_id"])
    op.create_index("ix_flitspalen_camera_route_sc_id", "flitspalen_camera_route", ["sc_id"])


def downgrade() -> None:
    op.drop_index("ix_flitspalen_camera_route_sc_id", table_name="flitspalen_camera_route")
    op.drop_constraint("flitspalen_camera_route_pkey", "flitspalen_camera_route", type_="primary")
    op.create_primary_key("flitspalen_camera_route_pkey", "flitspalen_camera_route", ["sc_id"])
