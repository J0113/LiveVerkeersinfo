"""Traffic speed and travel-time endpoints."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, func, select, text

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
    """Return one GeoJSON feature per location with merged per-lane speed/flow.

    Filters to anyVehicle aggregate measurements (no vehicle-length constraint)
    so each lane yields exactly one speed and one flow value.

    Multiple measurement systems (MONIBAS aggregate, MONICA per-lane, regional
    nets) can sit at the same point. We merge them **per lane**: readings sharing
    the latest timestamp are averaged; otherwise the latest non-null/non-zero
    reading wins. One marker per (location, side) instead of stacked duplicates.
    """
    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            TrafficMeasurement.site_id,
            MeasurementSite.num_lanes,
            MeasurementSite.side,
            MeasurementSite.road,
            MeasurementSite.carriageway,
            MeasurementSite.km,
            MeasurementSite.openlr_bearing,
            MeasurementCharacteristic.lane,
            func.max(
                case((TrafficMeasurement.value_type == "TrafficSpeed", TrafficMeasurement.speed_kmh))
            ).label("speed_kmh"),
            func.max(
                case((TrafficMeasurement.value_type == "TrafficFlow", TrafficMeasurement.flow_veh_h))
            ).label("flow_veh_h"),
            func.max(
                case((TrafficMeasurement.value_type == "TrafficSpeed", TrafficMeasurement.n_inputs))
            ).label("n_inputs"),
            func.max(
                case((TrafficMeasurement.value_type == "TrafficSpeed", TrafficMeasurement.std_dev))
            ).label("std_dev"),
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
            MeasurementSite.road,
            MeasurementSite.carriageway,
            MeasurementSite.km,
            MeasurementSite.openlr_bearing,
            MeasurementCharacteristic.lane,
            MeasurementSite.geom,
        )
        .order_by(TrafficMeasurement.site_id, MeasurementCharacteristic.lane)
    ).all()

    # Bucket lane readings by (location, side). Location keyed on geom rounded to
    # ~1m so genuinely co-located sites from different systems merge, but distinct
    # sites stay separate.
    locs: dict[tuple, dict] = {}
    for r in rows:
        if not r.geom_json:
            continue
        coords = tuple(round(c, 5) for c in json.loads(r.geom_json)["coordinates"])
        key = (coords, r.side)
        loc = locs.get(key)
        if loc is None:
            loc = locs[key] = {
                "coords": coords,
                "side": r.side,
                "road": r.road,
                "carriageway": r.carriageway,
                "km": float(r.km) if r.km is not None else None,
                "openlr_bearing": int(r.openlr_bearing) if r.openlr_bearing is not None else None,
                "num_lanes": r.num_lanes or 0,
                "sources": set(),
                "lanes": defaultdict(list),  # lane -> list of readings
            }
        loc["num_lanes"] = max(loc["num_lanes"], r.num_lanes or 0)
        if loc["road"] is None and r.road is not None:
            loc["road"] = r.road
            loc["carriageway"] = r.carriageway
            loc["km"] = float(r.km) if r.km is not None else None
        if loc["openlr_bearing"] is None and r.openlr_bearing is not None:
            loc["openlr_bearing"] = int(r.openlr_bearing)
        loc["sources"].add(r.site_id)
        loc["lanes"][r.lane].append({
            "speed": float(r.speed_kmh) if r.speed_kmh is not None else None,
            "flow": float(r.flow_veh_h) if r.flow_veh_h is not None else None,
            "n_inputs": int(r.n_inputs) if r.n_inputs is not None else None,
            "std_dev": float(r.std_dev) if r.std_dev is not None else None,
            "ts": r.measured_at,
        })

    features = []
    for loc in locs.values():
        if len(features) >= limit:
            break
        lanes_out = []
        feat_ts = None
        for lane in sorted(loc["lanes"], key=lambda x: (x is None, x)):
            m = _merge_lane(loc["lanes"][lane])
            lanes_out.append({
                "lane": lane,
                "speed_kmh": m["speed_kmh"],
                "flow_veh_h": m["flow_veh_h"],
                "n_inputs": m["n_inputs"],
                "std_dev": m["std_dev"],
            })
            if m["ts"] and (feat_ts is None or m["ts"] > feat_ts):
                feat_ts = m["ts"]

        sources = sorted(loc["sources"])
        systems = sorted({s.split("_")[1] for s in sources if "_" in s})
        # Prefer the MONIBAS aggregate id as the representative site_id.
        rep = next((s for s in sources if "_MONIBAS_" in s), sources[0] if sources else None)

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": list(loc["coords"])},
            "properties": {
                "site_id": rep,
                "road": loc["road"],
                "carriageway": loc["carriageway"],
                "km": loc["km"],
                "openlr_bearing": loc["openlr_bearing"],
                "num_lanes": loc["num_lanes"] or None,
                "side": loc["side"],
                "measured_at": feat_ts.isoformat() if feat_ts else None,
                "systems": systems,
                "source_count": len(sources),
                "lanes": lanes_out,
            },
        })

    _attach_bearings(db, features)
    return geo_response({"type": "FeatureCollection", "features": features})


def _attach_bearings(db, features: list[dict]) -> None:
    """Set props['bearing'] per feature.

    Uses openlr_bearing from the site record when available (parsed at ingest
    from the DATEX v2 OpenLR extension). Falls back to a nearest-neighbour
    azimuth against the meetlocatie_vak linestring table for sites without it.
    Removes the intermediate openlr_bearing key from output properties.
    """
    if not features:
        return

    need_spatial: list[tuple[int, dict]] = []  # (0-based index, feature)
    for idx, f in enumerate(features):
        ob = f["properties"].pop("openlr_bearing", None)
        if ob is not None:
            f["properties"]["bearing"] = int(ob) % 360
        else:
            need_spatial.append((idx, f))

    if not need_spatial:
        return

    lons = [f["geometry"]["coordinates"][0] for _, f in need_spatial]
    lats = [f["geometry"]["coordinates"][1] for _, f in need_spatial]
    rows = db.execute(
        text(
            """
            SELECT u.i,
                   degrees(ST_Azimuth(ST_StartPoint(v.geom), ST_EndPoint(v.geom))) AS bearing
            FROM unnest(CAST(:lons AS float8[]), CAST(:lats AS float8[]))
                 WITH ORDINALITY AS u(lon, lat, i)
            CROSS JOIN LATERAL (
                SELECT geom FROM meetlocatie_vak
                ORDER BY geom <-> ST_SetSRID(ST_MakePoint(u.lon, u.lat), 4326)
                LIMIT 1
            ) v
            """
        ),
        {"lons": lons, "lats": lats},
    ).all()
    spatial_bearings = {int(i): (round(float(b) % 360, 1) if b is not None else None) for i, b in rows}
    for local_i, (_, f) in enumerate(need_spatial, start=1):
        f["properties"]["bearing"] = spatial_bearings.get(local_i)


def _merge_lane(readings: list[dict]) -> dict:
    """Merge multiple sensor readings for one lane.

    Same-timestamp readings (different systems reporting concurrently) are
    averaged; otherwise the latest reading with a non-null, non-zero speed wins.
    Falls back to the latest reading of any value so the lane still appears.
    """
    valid = [x for x in readings if x["speed"] not in (None, 0) and x["ts"] is not None]
    if valid:
        latest = max(x["ts"] for x in valid)
        same = [x for x in valid if x["ts"] == latest]
        speeds = [x["speed"] for x in same]
        flows = [x["flow"] for x in same if x["flow"] is not None]
        n_inputs = [x["n_inputs"] for x in same if x["n_inputs"] is not None]
        stds = [x["std_dev"] for x in same if x["std_dev"] is not None]
        return {
            "speed_kmh": round(sum(speeds) / len(speeds), 1),
            "flow_veh_h": round(sum(flows) / len(flows)) if flows else None,
            # Summed sensor count, mean of reported deviations across merged systems.
            "n_inputs": sum(n_inputs) if n_inputs else None,
            "std_dev": round(sum(stds) / len(stds), 2) if stds else None,
            "ts": latest,
        }

    timed = [x for x in readings if x["ts"] is not None]
    pick = max(timed, key=lambda x: x["ts"]) if timed else readings[0]
    return {
        "speed_kmh": pick["speed"],
        "flow_veh_h": pick["flow"],
        "n_inputs": pick["n_inputs"],
        "std_dev": pick["std_dev"],
        "ts": pick["ts"],
    }


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
            TravelTime.quality,
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
            "quality": r.quality,
        }

    return geo_response(make_fc(rows, "geom_json", props))
