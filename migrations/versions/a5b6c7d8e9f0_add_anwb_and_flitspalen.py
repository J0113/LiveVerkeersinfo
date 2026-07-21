"""add anwb_incident and flitspalen_camera tables

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-07-21 00:00:00.000000
"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "anwb_incident",
        sa.Column("record_id", sa.String(), primary_key=True),
        sa.Column("id", sa.BigInteger()),
        sa.Column("category", sa.String()),
        sa.Column("incident_type", sa.String()),
        sa.Column("road", sa.String()),
        sa.Column("from_label", sa.String()),
        sa.Column("to_label", sa.String()),
        sa.Column("reason", sa.Text()),
        sa.Column("distance_m", sa.Integer()),
        sa.Column("delay_s", sa.Integer()),
        sa.Column("hm", sa.Numeric()),
        sa.Column("code_direction", sa.Integer()),
        sa.Column("segment_id", sa.Integer()),
        sa.Column("label", sa.String()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("poll_time", sa.DateTime(timezone=True)),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, spatial_index=False),
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_anwb_incident_geom", "anwb_incident", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_anwb_incident_category", "anwb_incident", ["category"])
    op.create_index("ix_anwb_incident_road", "anwb_incident", ["road"])
    op.create_index("ix_anwb_incident_id", "anwb_incident", ["id"])

    op.create_table(
        "flitspalen_camera",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("status", sa.String()),
        sa.Column("city", sa.String()),
        sa.Column("street", sa.String()),
        sa.Column("description", sa.Text()),
        sa.Column("speed_limit_kmh", sa.Integer()),
        sa.Column("camera_type", sa.String()),
        sa.Column("rotatable", sa.Boolean()),
        sa.Column("bearing_deg", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column(
            "geom",
            geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_flitspalen_camera_geom", "flitspalen_camera", ["geom"], postgresql_using="gist"
    )
    op.create_index("ix_flitspalen_camera_city", "flitspalen_camera", ["city"])


def downgrade() -> None:
    op.drop_table("flitspalen_camera")
    op.drop_table("anwb_incident")
