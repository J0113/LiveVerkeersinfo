"""SQLAlchemy ORM models — all NDW feed tables."""

from datetime import date, datetime
from typing import Any, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ndwinfo.db import Base

_tz = DateTime(timezone=True)


# ---------------------------------------------------------------------------
# Reference / static
# ---------------------------------------------------------------------------


class MeasurementSite(Base):
    __tablename__ = "measurement_site"
    __table_args__ = (Index("ix_measurement_site_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String)
    equipment_type: Mapped[Optional[str]] = mapped_column(String)
    num_lanes: Mapped[Optional[int]] = mapped_column(Integer)
    side: Mapped[Optional[str]] = mapped_column(String)
    version: Mapped[Optional[int]] = mapped_column(Integer)
    record_version_time: Mapped[Optional[datetime]] = mapped_column(_tz)
    road: Mapped[Optional[str]] = mapped_column(String)
    carriageway: Mapped[Optional[str]] = mapped_column(String)
    carriageway_source: Mapped[Optional[str]] = mapped_column(String)
    vild_carriageway: Mapped[Optional[str]] = mapped_column(String)
    vild_carriageway_source: Mapped[Optional[str]] = mapped_column(String)
    carriageway_direction_conflict: Mapped[Optional[bool]] = mapped_column(Boolean)
    km: Mapped[Optional[Any]] = mapped_column(Numeric)
    openlr_bearing: Mapped[Optional[int]] = mapped_column(Integer)
    vild_bearing: Mapped[Optional[Any]] = mapped_column(Numeric)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    # Segment line for travel-time sites: road-following (built from VILD TMC
    # chain) when resolvable, else straight start→end chord.
    line_geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=True
    )
    # AlertC/TMC location codes for travel-time segments (primary→secondary) +
    # travel direction; used to trace the road via the VILD TMC table.
    tmc_primary: Mapped[Optional[int]] = mapped_column(Integer)
    tmc_secondary: Mapped[Optional[int]] = mapped_column(Integer)
    tmc_direction: Mapped[Optional[str]] = mapped_column(String)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    characteristics: Mapped[list["MeasurementCharacteristic"]] = relationship(back_populates="site")
    measurements: Mapped[list["TrafficMeasurement"]] = relationship(back_populates="site")


class MeasurementCharacteristic(Base):
    __tablename__ = "measurement_characteristic"
    __table_args__ = (PrimaryKeyConstraint("site_id", "index"),)

    site_id: Mapped[str] = mapped_column(String, ForeignKey("measurement_site.id"))
    index: Mapped[int] = mapped_column(Integer)
    lane: Mapped[Optional[int]] = mapped_column(Integer)
    period_s: Mapped[Optional[int]] = mapped_column(Integer)
    value_type: Mapped[Optional[str]] = mapped_column(String)
    veh_length_min: Mapped[Optional[Any]] = mapped_column(Numeric)
    veh_length_max: Mapped[Optional[Any]] = mapped_column(Numeric)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    site: Mapped["MeasurementSite"] = relationship(back_populates="characteristics")


class MeetlocatiePunt(Base):
    __tablename__ = "meetlocatie_punt"
    __table_args__ = (Index("ix_meetlocatie_punt_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class MeetlocatieVak(Base):
    __tablename__ = "meetlocatie_vak"
    __table_args__ = (Index("ix_meetlocatie_vak_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class VildPoint(Base):
    __tablename__ = "vild_point"
    __table_args__ = (Index("ix_vild_point_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class VildLine(Base):
    __tablename__ = "vild_line"
    __table_args__ = (Index("ix_vild_line_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class VildTmc(Base):
    """VILD TMC location table (VILD6.x.A.dbf) — location-code topology.

    pos_off/neg_off chain consecutive TMC points along a road; lin_ref links a
    point to its road line (vild_line.id). Used to trace travel-time segments
    along the actual road between their primary and secondary location codes.
    """

    __tablename__ = "vild_tmc"

    loc_nr: Mapped[int] = mapped_column(Integer, primary_key=True)
    lin_ref: Mapped[Optional[int]] = mapped_column(Integer)
    pos_off: Mapped[Optional[int]] = mapped_column(Integer)
    neg_off: Mapped[Optional[int]] = mapped_column(Integer)
    road_number: Mapped[Optional[str]] = mapped_column(String)
    hecto_dir: Mapped[Optional[int]] = mapped_column(Integer)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class VildArea(Base):
    __tablename__ = "vild_area"
    __table_args__ = (Index("ix_vild_area_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class OsmRoad(Base):
    """OSM driving-road ways (motorway/trunk/primary/secondary + _link).

    osm_id is the OSM way id -- globally unique across all of OSM, used
    directly as PK. A way can appear in more than one Geofabrik extract
    (boundary-crossing ways are kept whole in each overlapping extract), so
    membership is tracked separately in OsmRoadExtract rather than deleting
    by a single run's timestamp.
    """

    __tablename__ = "osm_road"
    __table_args__ = (Index("ix_osm_road_geom", "geom", postgresql_using="gist"),)

    osm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    highway: Mapped[Optional[str]] = mapped_column(
        String
    )  # motorway|trunk|primary|secondary(+_link)
    name: Mapped[Optional[str]] = mapped_column(String)
    ref: Mapped[Optional[str]] = mapped_column(String)  # route number, e.g. A7, N99
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)  # full OSM tag dict, verbatim
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class OsmRoadExtract(Base):
    """Which Geofabrik extract(s) last confirmed seeing a given way.

    Lets ingesting one extract prune only its own stale memberships, never
    another extract's. OsmRoad rows with zero remaining memberships are the
    only ones eligible for deletion.
    """

    __tablename__ = "osm_road_extract"
    __table_args__ = (PrimaryKeyConstraint("extract_key", "osm_id"),)

    extract_key: Mapped[str] = mapped_column(String)  # e.g. "noord-holland", later "netherlands"
    osm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("osm_road.osm_id"))
    # bulk_upsert() stamps this on every row automatically (see ingest/base.py) --
    # doubles as "last confirmed present in this extract" for pruning.
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class OsmRoadLane(Base):
    """One offset lane centerline derived from an osm_road way + its lanes tag.

    id = f"{source_id}:{direction}:{lane}". source_id has ON DELETE CASCADE
    to osm_road so pruning a stale way (OsmRoadIngester's extract-scoped
    prune) automatically drops its lanes -- no separate lane-level prune.
    """

    __tablename__ = "osm_road_lane"
    __table_args__ = (
        Index("ix_osm_road_lane_geom", "geom", postgresql_using="gist"),
        Index("ix_osm_road_lane_source_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("osm_road.osm_id", ondelete="CASCADE")
    )
    lane: Mapped[int] = mapped_column(Integer)  # 1-indexed physical position, left to right
    lane_count: Mapped[int] = mapped_column(
        Integer
    )  # total lanes this row's direction contributes to
    direction: Mapped[Optional[str]] = mapped_column(String)  # fwd|bwd|unknown
    role: Mapped[Optional[str]] = mapped_column(
        String
    )  # normal|merge_left|merge_right|both_ways|unknown
    highway: Mapped[Optional[str]] = mapped_column(String)
    name: Mapped[Optional[str]] = mapped_column(
        String
    )  # denormalized from parent way, like highway
    ref: Mapped[Optional[str]] = mapped_column(String)
    width_m: Mapped[Optional[Any]] = mapped_column(Numeric)  # 3.5 or 2.75
    # offset_curve() can return MultiLineString, so this is GEOMETRY rather
    # than LINESTRING.
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# Real-time measurement (latest per site+index)
# ---------------------------------------------------------------------------


class TrafficMeasurement(Base):
    __tablename__ = "traffic_measurement"
    __table_args__ = (PrimaryKeyConstraint("site_id", "index"),)

    site_id: Mapped[str] = mapped_column(String, ForeignKey("measurement_site.id"))
    index: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[Optional[datetime]] = mapped_column(_tz)
    value_type: Mapped[Optional[str]] = mapped_column(String)
    flow_veh_h: Mapped[Optional[Any]] = mapped_column(Numeric)
    speed_kmh: Mapped[Optional[Any]] = mapped_column(Numeric)
    n_inputs: Mapped[Optional[int]] = mapped_column(Integer)
    std_dev: Mapped[Optional[Any]] = mapped_column(Numeric)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    site: Mapped["MeasurementSite"] = relationship(back_populates="measurements")


class TravelTime(Base):
    __tablename__ = "travel_time"
    __table_args__ = (PrimaryKeyConstraint("segment_id", "index"),)

    segment_id: Mapped[str] = mapped_column(String)
    index: Mapped[int] = mapped_column(Integer)
    measured_at: Mapped[Optional[datetime]] = mapped_column(_tz)
    duration_s: Mapped[Optional[Any]] = mapped_column(Numeric)
    ref_duration_s: Mapped[Optional[Any]] = mapped_column(Numeric)
    accuracy: Mapped[Optional[Any]] = mapped_column(Numeric)
    n_inputs: Mapped[Optional[int]] = mapped_column(Integer)
    quality: Mapped[Optional[str]] = mapped_column(String)
    travel_time_type: Mapped[Optional[str]] = mapped_column(String)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# Situations — one table for all 6 DATEX v3 situation feeds
# ---------------------------------------------------------------------------


class Situation(Base):
    __tablename__ = "situation"
    __table_args__ = (
        Index("ix_situation_geom", "geom", postgresql_using="gist"),
        Index("ix_situation_category", "category"),
        Index("ix_situation_id", "id"),
    )

    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[Optional[str]] = mapped_column(String)  # situation grouping id
    category: Mapped[Optional[str]] = mapped_column(String)  # incident|srti|roadworks|...
    record_type: Mapped[Optional[str]] = mapped_column(String)  # xsi:type stripped
    severity: Mapped[Optional[str]] = mapped_column(String)
    probability: Mapped[Optional[str]] = mapped_column(String)
    safety_related: Mapped[Optional[bool]] = mapped_column(Boolean)
    source: Mapped[Optional[str]] = mapped_column(String)
    valid_from: Mapped[Optional[datetime]] = mapped_column(_tz)
    valid_to: Mapped[Optional[datetime]] = mapped_column(_tz)
    version_time: Mapped[Optional[datetime]] = mapped_column(_tz)
    speed_limit_kmh: Mapped[Optional[int]] = mapped_column(Integer)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# ANWB incidents (jams / roadworks / dynamic radars) + Flitspalen static cameras
# ---------------------------------------------------------------------------


class AnwbIncident(Base):
    __tablename__ = "anwb_incident"
    __table_args__ = (
        Index("ix_anwb_incident_geom", "geom", postgresql_using="gist"),
        Index("ix_anwb_incident_category", "category"),
        Index("ix_anwb_incident_road", "road"),
        Index("ix_anwb_incident_id", "id"),
    )

    record_id: Mapped[str] = mapped_column(String, primary_key=True)  # f"{category}:{id}"
    id: Mapped[Optional[int]] = mapped_column(BigInteger)
    category: Mapped[Optional[str]] = mapped_column(String)  # jams|roadworks|radars
    incident_type: Mapped[Optional[str]] = mapped_column(String)
    road: Mapped[Optional[str]] = mapped_column(String)
    from_label: Mapped[Optional[str]] = mapped_column(String)
    to_label: Mapped[Optional[str]] = mapped_column(String)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    distance_m: Mapped[Optional[int]] = mapped_column(Integer)
    delay_s: Mapped[Optional[int]] = mapped_column(Integer)
    hm: Mapped[Optional[Any]] = mapped_column(Numeric)
    code_direction: Mapped[Optional[int]] = mapped_column(Integer)
    segment_id: Mapped[Optional[int]] = mapped_column(Integer)
    label: Mapped[Optional[str]] = mapped_column(String)
    valid_from: Mapped[Optional[datetime]] = mapped_column(_tz)
    poll_time: Mapped[Optional[datetime]] = mapped_column(_tz)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class FlitspalenCamera(Base):
    __tablename__ = "flitspalen_camera"
    __table_args__ = (
        Index("ix_flitspalen_camera_geom", "geom", postgresql_using="gist"),
        Index("ix_flitspalen_camera_city", "city"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[Optional[str]] = mapped_column(String)  # only "A"/actief is ingested
    city: Mapped[Optional[str]] = mapped_column(String)
    street: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    speed_limit_kmh: Mapped[Optional[int]] = mapped_column(Integer)
    camera_type: Mapped[Optional[str]] = mapped_column(String)
    rotatable: Mapped[Optional[bool]] = mapped_column(Boolean)
    bearing_deg: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(_tz)
    edited_at: Mapped[Optional[datetime]] = mapped_column(_tz)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


class FlitspalenCameraRoute(Base):
    """Road-snapped path between one SC (entry) and its paired SCE (exit)
    trajectcontrole camera, precomputed at ingest time against osm_road so the
    line traces the carriageway instead of cutting cross-country between the
    two camera points. See ingest/flitspalen_route.py for the pairing/matching.
    """

    __tablename__ = "flitspalen_camera_route"
    __table_args__ = (Index("ix_flitspalen_camera_route_geom", "geom", postgresql_using="gist"),)

    sc_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sce_id: Mapped[int] = mapped_column(BigInteger)
    street: Mapped[Optional[str]] = mapped_column(String)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# Signs & VMS
# ---------------------------------------------------------------------------


class MsiSign(Base):
    __tablename__ = "msi_sign"
    __table_args__ = (Index("ix_msi_sign_geom", "geom", postgresql_using="gist"),)

    uuid: Mapped[str] = mapped_column(String, primary_key=True)
    road: Mapped[Optional[str]] = mapped_column(String)
    carriageway: Mapped[Optional[str]] = mapped_column(String)
    lane: Mapped[Optional[int]] = mapped_column(Integer)
    km: Mapped[Optional[Any]] = mapped_column(Numeric)
    # Road heading at the sign (degrees, clockwise from north); from MSI shapefile.
    # Used by the UI to offset the sign perpendicular to the road (draw it roadside).
    bearing: Mapped[Optional[float]] = mapped_column(Numeric)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    state: Mapped[Optional["MsiState"]] = relationship(back_populates="sign")


class MsiState(Base):
    __tablename__ = "msi_state"

    uuid: Mapped[str] = mapped_column(String, ForeignKey("msi_sign.uuid"), primary_key=True)
    ts_state: Mapped[Optional[datetime]] = mapped_column(_tz)
    aspect_type: Mapped[Optional[str]] = mapped_column(String)
    value: Mapped[Optional[str]] = mapped_column(String)
    flashing: Mapped[Optional[bool]] = mapped_column(Boolean)
    red_ring: Mapped[Optional[bool]] = mapped_column(Boolean)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    sign: Mapped["MsiSign"] = relationship(back_populates="state")


class Drip(Base):
    __tablename__ = "drip"
    __table_args__ = (
        PrimaryKeyConstraint("controller_id", "vms_index"),
        Index("ix_drip_geom", "geom", postgresql_using="gist"),
    )

    controller_id: Mapped[str] = mapped_column(String)
    vms_index: Mapped[int] = mapped_column(Integer)
    description: Mapped[Optional[str]] = mapped_column(Text)
    vms_type: Mapped[Optional[str]] = mapped_column(String)
    physical_support: Mapped[Optional[str]] = mapped_column(String)
    bearing: Mapped[Optional[int]] = mapped_column(Integer)
    num_display_areas: Mapped[Optional[int]] = mapped_column(Integer)
    display_text: Mapped[Optional[str]] = mapped_column(Text)
    message: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# EV charging
# ---------------------------------------------------------------------------


class ChargePoint(Base):
    __tablename__ = "charge_point"
    __table_args__ = (Index("ix_charge_point_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cpo_id: Mapped[Optional[str]] = mapped_column(String)
    address: Mapped[Optional[str]] = mapped_column(String)
    city: Mapped[Optional[str]] = mapped_column(String)
    operator_name: Mapped[Optional[str]] = mapped_column(String)
    owner_name: Mapped[Optional[str]] = mapped_column(String)
    open: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_updated: Mapped[Optional[datetime]] = mapped_column(_tz)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    availability: Mapped[list["ChargeAvailability"]] = relationship(back_populates="cp")


class ChargeAvailability(Base):
    __tablename__ = "charge_availability"
    __table_args__ = (PrimaryKeyConstraint("cp_id", "idx"),)

    cp_id: Mapped[str] = mapped_column(String, ForeignKey("charge_point.id"))
    idx: Mapped[int] = mapped_column(Integer)
    total: Mapped[Optional[int]] = mapped_column(Integer)
    available: Mapped[Optional[int]] = mapped_column(Integer)
    power_max: Mapped[Optional[Any]] = mapped_column(Numeric)
    power_type: Mapped[Optional[str]] = mapped_column(String)
    connector_type: Mapped[Optional[str]] = mapped_column(String)
    connector_format: Mapped[Optional[str]] = mapped_column(String)
    tariff_ids: Mapped[Optional[Any]] = mapped_column(ARRAY(Text()), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    cp: Mapped["ChargePoint"] = relationship(back_populates="availability")


class Tariff(Base):
    __tablename__ = "tariff"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[Optional[str]] = mapped_column(String)
    party_id: Mapped[Optional[str]] = mapped_column(String)
    country_code: Mapped[Optional[str]] = mapped_column(String)
    elements: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(_tz)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# Truck parking
# ---------------------------------------------------------------------------


class TruckParking(Base):
    __tablename__ = "truck_parking"
    __table_args__ = (Index("ix_truck_parking_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String)
    operator: Mapped[Optional[str]] = mapped_column(String)
    capacity: Mapped[Optional[int]] = mapped_column(Integer)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    status: Mapped[Optional["TruckParkingStatus"]] = relationship(back_populates="parking")


class TruckParkingStatus(Base):
    __tablename__ = "truck_parking_status"

    parking_id: Mapped[str] = mapped_column(
        String, ForeignKey("truck_parking.id"), primary_key=True
    )
    origin_time: Mapped[Optional[datetime]] = mapped_column(_tz)
    vacant: Mapped[Optional[int]] = mapped_column(Integer)
    occupied: Mapped[Optional[int]] = mapped_column(Integer)
    occupancy_pct: Mapped[Optional[Any]] = mapped_column(Numeric)
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())

    parking: Mapped["TruckParking"] = relationship(back_populates="status")


# ---------------------------------------------------------------------------
# Traffic signs (verkeersborden)
# ---------------------------------------------------------------------------


class TrafficSign(Base):
    __tablename__ = "traffic_sign"
    __table_args__ = (Index("ix_traffic_sign_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    rvv_code: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[Optional[str]] = mapped_column(String)
    placement: Mapped[Optional[str]] = mapped_column(String)
    side: Mapped[Optional[str]] = mapped_column(String)
    bearing: Mapped[Optional[int]] = mapped_column(Integer)
    driving_direction: Mapped[Optional[str]] = mapped_column(String)
    fraction: Mapped[Optional[Any]] = mapped_column(Numeric)
    road_name: Mapped[Optional[str]] = mapped_column(String)
    road_section_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    nwb_version: Mapped[Optional[str]] = mapped_column(String)
    county_code: Mapped[Optional[str]] = mapped_column(String)
    county_name: Mapped[Optional[str]] = mapped_column(String)
    town_name: Mapped[Optional[str]] = mapped_column(String)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    text_signs: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    first_seen: Mapped[Optional[date]] = mapped_column(Date)
    last_seen: Mapped[Optional[date]] = mapped_column(Date)
    placed_on: Mapped[Optional[date]] = mapped_column(Date)
    removed_on: Mapped[Optional[date]] = mapped_column(Date)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# Emission zones (DATEX v3 ControlledZoneTablePublication)
# ---------------------------------------------------------------------------


class EmissionZone(Base):
    __tablename__ = "emission_zone"
    __table_args__ = (Index("ix_emission_zone_geom", "geom", postgresql_using="gist"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String)
    zone_type: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[Optional[str]] = mapped_column(String)
    authority: Mapped[Optional[str]] = mapped_column(String)
    info_url: Mapped[Optional[str]] = mapped_column(Text)
    geom: Mapped[Optional[Any]] = mapped_column(
        Geometry("POLYGON", srid=4326, spatial_index=False), nullable=True
    )
    raw: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(_tz, server_default=func.now())


# ---------------------------------------------------------------------------
# Operational
# ---------------------------------------------------------------------------


class FeedRun(Base):
    __tablename__ = "feed_run"
    __table_args__ = (Index("ix_feed_run_feed_finished", "feed", "finished_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed: Mapped[str] = mapped_column(String)
    started_at: Mapped[Optional[datetime]] = mapped_column(_tz)
    finished_at: Mapped[Optional[datetime]] = mapped_column(_tz)
    status: Mapped[Optional[str]] = mapped_column(String)  # ok|not_modified|error
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    etag: Mapped[Optional[str]] = mapped_column(String)
    last_modified: Mapped[Optional[str]] = mapped_column(String)
    rows_upserted: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)


class SystemState(Base):
    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    last_api_request_at: Mapped[Optional[datetime]] = mapped_column(_tz, nullable=True)
