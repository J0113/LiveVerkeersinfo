"""Traffic speed and travel-time endpoints."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import and_, case, func, select, text

from ndwinfo.api.deps import BBoxDep, DbDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import (
    MeasurementCharacteristic,
    MeasurementSite,
    TrafficMeasurement,
    TravelTime,
    WeggegLane,
)

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
                case(
                    (
                        TrafficMeasurement.value_type == "TrafficSpeed",
                        TrafficMeasurement.speed_kmh,
                    )
                )
            ).label("speed_kmh"),
            func.max(
                case(
                    (
                        TrafficMeasurement.value_type == "TrafficFlow",
                        TrafficMeasurement.flow_veh_h,
                    )
                )
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

    # Identify road/carriageway metadata available at each physical point first.
    # Some MONICA records omit road/km but are exactly co-located with a MONIBAS
    # record that has them. Inherit only when there is one unambiguous candidate.
    known_at_position: dict[tuple, set[tuple[str, str | None]]] = defaultdict(set)
    for row in rows:
        if not row.geom_json or not row.road:
            continue
        row_coords = tuple(round(c, 5) for c in json.loads(row.geom_json)["coordinates"])
        known_at_position[(row_coords, row.side)].add((row.road, row.carriageway))

    # Bucket lane readings by physical location + road direction. Location is
    # rounded to ~1m so co-located systems merge, while carriageways stay apart.
    locs: dict[tuple, dict] = {}
    for r in rows:
        if not r.geom_json:
            continue
        coords = tuple(round(c, 5) for c in json.loads(r.geom_json)["coordinates"])
        effective_road = r.road
        effective_carriageway = r.carriageway
        inherited = known_at_position.get((coords, r.side), set())
        if effective_road is None and len(inherited) == 1:
            effective_road, effective_carriageway = next(iter(inherited))
        # Keep opposite carriageways separate even when two systems publish the
        # same gantry coordinate and measurementSide is absent.
        key = (coords, effective_road, effective_carriageway, r.side)
        loc = locs.get(key)
        if loc is None:
            loc = locs[key] = {
                "coords": coords,
                "side": r.side,
                "road": effective_road,
                "carriageway": effective_carriageway,
                "km": float(r.km) if r.km is not None else None,
                "openlr_bearing": int(r.openlr_bearing) if r.openlr_bearing is not None else None,
                "num_lanes": r.num_lanes or 0,
                "sources": set(),
                "lanes": defaultdict(list),  # lane -> list of readings
            }
        loc["num_lanes"] = max(loc["num_lanes"], r.num_lanes or 0)
        if r.road is not None and (loc["road"] is None or loc["km"] is None):
            loc["road"] = r.road
            loc["carriageway"] = r.carriageway
            loc["km"] = float(r.km) if r.km is not None else loc["km"]
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

    _attach_weggeg_matches(db, features)
    _attach_fallback_bearings(db, features)
    return geo_response({"type": "FeatureCollection", "features": features})


def _normalized_road_number(value: str | None) -> str | None:
    """Normalize A1/N001/001 to WEGGEG's zero-padded numeric road key."""
    if not value:
        return None
    match = re.search(r"\d+", value)
    return f"{int(match.group()):03d}" if match else None


def _attach_weggeg_matches(db, features: list[dict]) -> None:
    """Match speed locations to WEGGEG and attach a local travel bearing.

    Road number, carriageway, and hectometre range make the semantic match;
    distance (capped at 100m) disambiguates parallel/overlapping sections. The
    WEGGEG geometry is digitised with increasing kilometre, so T/L carriageways
    are reversed to obtain the actual travel direction. At interchanges, where
    NDW can retain the old road identity after the physical lane has become a
    different WEGGEG road, a tightly constrained geometry fallback is used.
    """
    payload = []
    for index, feature in enumerate(features):
        props = feature["properties"]
        road_number = _normalized_road_number(props.get("road"))
        carriageway = props.get("carriageway")
        km = props.get("km")
        if road_number is None or carriageway not in {"R", "L"} or km is None:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        payload.append({
            "i": index,
            "lon": lon,
            "lat": lat,
            "road_number": road_number,
            "carriageway": carriageway,
            "km": km,
        })

    if not payload:
        return

    rows = db.execute(
        text(
            """
            WITH sites AS (
                SELECT s.*,
                       ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326) AS point
                FROM jsonb_to_recordset(CAST(:sites AS jsonb)) AS s(
                    i integer,
                    lon double precision,
                    lat double precision,
                    road_number text,
                    carriageway text,
                    km double precision
                )
            )
            SELECT s.i,
                   candidate.source_id,
                   candidate.lane_count,
                   candidate.distance_m,
                   mod((
                       degrees(ST_Azimuth(
                           ST_LineInterpolatePoint(
                               candidate.line,
                               greatest(candidate.fraction - 0.0001, 0)
                           ),
                           ST_LineInterpolatePoint(
                               candidate.line,
                               least(candidate.fraction + 0.0001, 1)
                           )
                       )) + CASE WHEN candidate.direction = 'T' THEN 180 ELSE 0 END
                   )::numeric, 360)::double precision AS bearing
            FROM sites s
            CROSS JOIN LATERAL (
                SELECT w.source_id,
                       w.lane_count,
                       w.direction,
                       merged.line,
                       ST_Distance(s.point::geography, w.geom::geography) AS distance_m,
                       ST_LineLocatePoint(merged.line, s.point) AS fraction
                FROM weggeg_lane w
                CROSS JOIN LATERAL (SELECT ST_LineMerge(w.geom) AS line) merged
                WHERE w.lane = 1
                  AND w.road_number = s.road_number
                  AND w.carriageway_side = s.carriageway
                  AND s.km BETWEEN
                      least((w.raw->>'BEGINKM')::double precision,
                            (w.raw->>'EINDKM')::double precision) - 0.25
                      AND
                      greatest((w.raw->>'BEGINKM')::double precision,
                               (w.raw->>'EINDKM')::double precision) + 0.25
                  AND GeometryType(merged.line) = 'LINESTRING'
                  AND ST_DWithin(w.geom::geography, s.point::geography, 100)
                ORDER BY w.geom <-> s.point
                LIMIT 1
            ) candidate
            """
        ),
        {"sites": json.dumps(payload)},
    ).all()

    matched_indices = set()
    for row in rows:
        props = features[row.i]["properties"]
        props["weggeg_source_id"] = row.source_id
        props["weggeg_lane_count"] = row.lane_count
        props["weggeg_distance_m"] = round(float(row.distance_m), 1)
        props["weggeg_match_method"] = "road_carriageway_km"
        if row.bearing is not None:
            props["bearing"] = round(float(row.bearing) % 360, 1)
            props["bearing_source"] = "weggeg"
        matched_indices.add(row.i)

    # Exit and connector measurements sometimes retain their former road/km
    # metadata while WEGGEG assigns the physical lane to the intersecting road.
    # Only accept an exact lane-count match within 25m, and reject it when a
    # second candidate is less than 5m farther away. This prevents snapping to
    # a parallel/opposite carriageway in dense interchange geometry.
    spatial_payload = []
    for index, feature in enumerate(features):
        if index in matched_indices:
            continue
        num_lanes = feature["properties"].get("num_lanes")
        if not isinstance(num_lanes, int) or num_lanes < 1:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        spatial_payload.append({
            "i": index,
            "lon": lon,
            "lat": lat,
            "num_lanes": num_lanes,
        })

    if not spatial_payload:
        return

    spatial_rows = db.execute(
        text(
            """
            WITH sites AS (
                SELECT s.*,
                       ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326) AS point
                FROM jsonb_to_recordset(CAST(:sites AS jsonb)) AS s(
                    i integer,
                    lon double precision,
                    lat double precision,
                    num_lanes integer
                )
            )
            SELECT s.i,
                   candidate.source_id,
                   candidate.lane_count,
                   candidate.road_number,
                   candidate.carriageway_side,
                   candidate.distance_m,
                   mod((
                       degrees(ST_Azimuth(
                           ST_LineInterpolatePoint(
                               candidate.line,
                               greatest(candidate.fraction - 0.0001, 0)
                           ),
                           ST_LineInterpolatePoint(
                               candidate.line,
                               least(candidate.fraction + 0.0001, 1)
                           )
                       )) + CASE WHEN candidate.direction = 'T' THEN 180 ELSE 0 END
                   )::numeric, 360)::double precision AS bearing
            FROM sites s
            CROSS JOIN LATERAL (
                SELECT w.source_id,
                       w.lane_count,
                       w.road_number,
                       w.carriageway_side,
                       w.direction,
                       merged.line,
                       ST_Distance(s.point::geography, w.geom::geography) AS distance_m,
                       ST_LineLocatePoint(merged.line, s.point) AS fraction
                FROM weggeg_lane w
                CROSS JOIN LATERAL (SELECT ST_LineMerge(w.geom) AS line) merged
                WHERE w.lane = 1
                  AND w.lane_count = s.num_lanes
                  AND GeometryType(merged.line) = 'LINESTRING'
                  AND ST_DWithin(w.geom::geography, s.point::geography, 25)
                ORDER BY w.geom <-> s.point
                LIMIT 2
            ) candidate
            ORDER BY s.i, candidate.distance_m
            """
        ),
        {"sites": json.dumps(spatial_payload)},
    ).all()

    candidates_by_index: dict[int, list] = defaultdict(list)
    for row in spatial_rows:
        candidates_by_index[row.i].append(row)

    for index, candidates in candidates_by_index.items():
        nearest = candidates[0]
        if (
            len(candidates) > 1
            and float(candidates[1].distance_m) < float(nearest.distance_m) + 5
        ):
            continue
        props = features[index]["properties"]
        props["weggeg_source_id"] = nearest.source_id
        props["weggeg_lane_count"] = nearest.lane_count
        props["weggeg_distance_m"] = round(float(nearest.distance_m), 1)
        props["weggeg_match_method"] = "unambiguous_geometry"
        props["weggeg_matched_road_number"] = nearest.road_number
        props["weggeg_matched_carriageway"] = nearest.carriageway_side
        if nearest.bearing is not None:
            props["bearing"] = round(float(nearest.bearing) % 360, 1)
            props["bearing_source"] = "weggeg"


def _attach_fallback_bearings(db, features: list[dict]) -> None:
    """Keep the prior OpenLR/nearest-line bearing when WEGGEG has no match.

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
        if f["properties"].get("bearing") is not None:
            continue
        if ob is not None:
            f["properties"]["bearing"] = int(ob) % 360
            f["properties"]["bearing_source"] = "openlr"
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
    spatial_bearings = {
        int(i): (round(float(b) % 360, 1) if b is not None else None)
        for i, b in rows
    }
    for local_i, (_, f) in enumerate(need_spatial, start=1):
        f["properties"]["bearing"] = spatial_bearings.get(local_i)
        f["properties"]["bearing_source"] = "meetlocatie_vak"


def _lane_speed_feature_collection(db, point_features: list[dict], b: BBoxDep) -> dict:
    """Project matched point measurements onto their separate WEGGEG lanes."""
    matched: dict[str, list[dict]] = defaultdict(list)
    for feature in point_features:
        source_id = feature["properties"].get("weggeg_source_id")
        if source_id:
            matched[source_id].append(feature)

    if not matched:
        return {"type": "FeatureCollection", "features": []}

    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            WeggegLane.id,
            WeggegLane.source_id,
            WeggegLane.lane,
            WeggegLane.lane_count,
            WeggegLane.road_number,
            WeggegLane.direction,
            WeggegLane.carriageway_side,
            func.ST_AsGeoJSON(WeggegLane.geom, 6).label("geom_json"),
        )
        .where(
            WeggegLane.source_id.in_(list(matched)),
            func.ST_Intersects(WeggegLane.geom, bbox_geom),
        )
        .order_by(WeggegLane.source_id, WeggegLane.lane)
        .limit(settings.api_max_limit)
    ).all()

    lane_features = []
    for row in rows:
        candidates: list[tuple[dict, dict]] = []
        for point in matched[row.source_id]:
            lane = next(
                (item for item in point["properties"].get("lanes", []) if item["lane"] == row.lane),
                None,
            )
            if lane is not None:
                candidates.append((point, lane))

        # Prefer a non-null speed, then the newest sample, then the closest
        # semantic/spatial WEGGEG match when several sensors cover one section.
        candidates.sort(
            key=lambda item: (
                item[1].get("speed_kmh") is not None,
                item[0]["properties"].get("measured_at") or "",
                -float(item[0]["properties"].get("weggeg_distance_m") or 9999),
            ),
            reverse=True,
        )
        point, lane_data = candidates[0] if candidates else (matched[row.source_id][0], {})
        point_props = point["properties"]
        lane_features.append({
            "type": "Feature",
            "geometry": json.loads(row.geom_json) if row.geom_json else None,
            "properties": {
                "id": row.id,
                "source_id": row.source_id,
                "lane": row.lane,
                "lane_count": row.lane_count,
                "road": point_props.get("road"),
                "road_number": row.road_number,
                "carriageway": point_props.get("carriageway"),
                "direction": row.direction,
                "km": point_props.get("km"),
                "site_id": point_props.get("site_id"),
                "measured_at": point_props.get("measured_at"),
                "bearing": point_props.get("bearing"),
                "measurement_coords": point["geometry"]["coordinates"],
                "speed_kmh": lane_data.get("speed_kmh"),
                "flow_veh_h": lane_data.get("flow_veh_h"),
                "n_inputs": lane_data.get("n_inputs"),
                "std_dev": lane_data.get("std_dev"),
            },
        })
    return {"type": "FeatureCollection", "features": lane_features}


@router.get("/speed/map")
def get_speed_map(
    b: BBoxDep,
    db: DbDep,
    include_lanes: bool = True,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    """Return matched WEGGEG speed lanes plus point fallbacks for the map."""
    point_response = get_speed(b=b, db=db, limit=limit)
    points = json.loads(point_response.body)
    lanes = (
        _lane_speed_feature_collection(db, points["features"], b)
        if include_lanes
        else {"type": "FeatureCollection", "features": []}
    )
    return Response(
        content=json.dumps({"points": points, "lanes": lanes}),
        media_type="application/json",
    )


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
            func.ST_AsGeoJSON(
                func.coalesce(MeasurementSite.line_geom, MeasurementSite.geom), 6
            ).label("geom_json"),
        )
        .join(MeasurementSite, TravelTime.segment_id == MeasurementSite.id)
        # Filter on the segment line (fall back to point) so a segment stays
        # visible when zoomed in between its endpoints — the line crosses the
        # viewport even if neither endpoint is inside it.
        .where(
            func.ST_Intersects(
                func.coalesce(MeasurementSite.line_geom, MeasurementSite.geom),
                bbox_geom,
            )
        )
        .limit(limit)
    ).all()

    def props(r):
        return {
            "fid": f"{r.segment_id}:{r.index}",  # stable id for map selection state
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
