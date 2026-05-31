"""Traffic speed and travel-time endpoints."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, func, select

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import MeasurementCharacteristic, MeasurementSite, TrafficMeasurement, TravelTime

router = APIRouter(prefix="/traffic", tags=["traffic"])


@router.get("/speed")
def get_speed(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    """Return one GeoJSON feature per measurement site with per-lane speed/flow.

    Filters to anyVehicle aggregate measurements (no vehicle-length constraint)
    so each lane yields exactly one speed and one flow value.
    """
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            TrafficMeasurement.site_id,
            MeasurementSite.num_lanes,
            MeasurementSite.side,
            MeasurementCharacteristic.lane,
            func.max(
                case((TrafficMeasurement.value_type == "TrafficSpeed", TrafficMeasurement.speed_kmh))
            ).label("speed_kmh"),
            func.max(
                case((TrafficMeasurement.value_type == "TrafficFlow", TrafficMeasurement.flow_veh_h))
            ).label("flow_veh_h"),
            func.max(TrafficMeasurement.measured_at).label("measured_at"),
            func.ST_AsGeoJSON(MeasurementSite.geom, 6).label("geom_json"),
        )
        .join(MeasurementSite, TrafficMeasurement.site_id == MeasurementSite.id)
        .join(
            MeasurementCharacteristic,
            and_(
                TrafficMeasurement.site_id == MeasurementCharacteristic.site_id,
                TrafficMeasurement.index == MeasurementCharacteristic.index,
            ),
        )
        .where(
            func.ST_Intersects(MeasurementSite.geom, bbox_geom),
            MeasurementCharacteristic.lane.isnot(None),
            MeasurementCharacteristic.veh_length_min.is_(None),
            MeasurementCharacteristic.veh_length_max.is_(None),
        )
        .group_by(
            TrafficMeasurement.site_id,
            MeasurementSite.num_lanes,
            MeasurementSite.side,
            MeasurementCharacteristic.lane,
            MeasurementSite.geom,
        )
        .order_by(TrafficMeasurement.site_id, MeasurementCharacteristic.lane)
    ).all()

    # Group lane rows into per-site features
    sites: dict[str, dict] = {}
    for r in rows:
        if r.site_id not in sites:
            if len(sites) >= limit:
                continue
            sites[r.site_id] = {
                "site_id": r.site_id,
                "num_lanes": r.num_lanes,
                "side": r.side,
                "measured_at": r.measured_at.isoformat() if r.measured_at else None,
                "geom_json": r.geom_json,
                "lanes": [],
            }
        sites[r.site_id]["lanes"].append({
            "lane": r.lane,
            "speed_kmh": float(r.speed_kmh) if r.speed_kmh is not None else None,
            "flow_veh_h": float(r.flow_veh_h) if r.flow_veh_h is not None else None,
        })

    features = []
    for s in sites.values():
        geom_json = s.pop("geom_json")
        if not geom_json:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(geom_json),
            "properties": s,
        })

    return geo_response({"type": "FeatureCollection", "features": features})


@router.get("/traveltime")
def get_traveltime(
    b: BBoxDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            TravelTime.segment_id,
            TravelTime.index,
            TravelTime.measured_at,
            TravelTime.travel_time_type,
            TravelTime.duration_s,
            TravelTime.ref_duration_s,
            TravelTime.accuracy,
            TravelTime.n_inputs,
            func.ST_AsGeoJSON(MeasurementSite.geom, 6).label("geom_json"),
        )
        .join(MeasurementSite, TravelTime.segment_id == MeasurementSite.id)
        .where(func.ST_Intersects(MeasurementSite.geom, bbox_geom))
        .limit(limit)
    ).all()

    def props(r):
        return {
            "segment_id": r.segment_id,
            "index": r.index,
            "measured_at": r.measured_at.isoformat() if r.measured_at else None,
            "travel_time_type": r.travel_time_type,
            "duration_s": float(r.duration_s) if r.duration_s is not None else None,
            "ref_duration_s": float(r.ref_duration_s) if r.ref_duration_s is not None else None,
            "accuracy": float(r.accuracy) if r.accuracy is not None else None,
            "n_inputs": r.n_inputs,
        }

    return geo_response(make_fc(rows, "geom_json", props))
