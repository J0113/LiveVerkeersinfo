"""Traffic speed and travel-time endpoints."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import and_, case, func, select, text

from ndwinfo.api.deps import BBox, BBoxDep, DbDep
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

WEGGEG_NEAR_MATCH_MAX_DISTANCE_M = 2.5
WEGGEG_WIDE_MATCH_MAX_DISTANCE_M = 25


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

    First prefer physical lane geometry within 2.5m, using road and carriageway
    only to rank multiple nearby candidates. For unmatched sites, widen to 25m,
    require road/hectometre agreement, and rank carriageway then lane-count
    agreement before distance. Bearing is derived after selection, never used
    as a matching input.
    """
    payload = []
    for index, feature in enumerate(features):
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        payload.append({
            "i": index,
            "lon": lon,
            "lat": lat,
            "road_number": _normalized_road_number(props.get("road")),
            "carriageway": props.get("carriageway"),
            "km": props.get("km"),
            "num_lanes": props.get("num_lanes"),
        })

    if not payload:
        return

    near_rows = db.execute(
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
                    km double precision,
                    num_lanes integer
                )
            )
            SELECT s.i,
                   w.source_id,
                   w.lane_count,
                   w.road_number,
                   w.carriageway_side,
                   (w.road_number = s.road_number) AS road_match,
                   (w.carriageway_side = s.carriageway) AS carriageway_match,
                   (w.lane_count = s.num_lanes) AS lane_count_match,
                   ST_Distance(s.point::geography, w.geom::geography) AS distance_m,
                   mod((
                       degrees(ST_Azimuth(
                           ST_LineInterpolatePoint(
                               merged.line,
                               greatest(ST_LineLocatePoint(merged.line, s.point) - 0.0001, 0)
                           ),
                           ST_LineInterpolatePoint(
                               merged.line,
                               least(ST_LineLocatePoint(merged.line, s.point) + 0.0001, 1)
                           )
                       )) + CASE WHEN w.direction = 'T' THEN 180 ELSE 0 END
                   )::numeric, 360)::double precision AS bearing,
                   opp.roadside_bearing
            FROM sites s
            JOIN weggeg_lane w
              ON w.lane = 1
             AND ST_DWithin(
                 w.geom::geography,
                 s.point::geography,
                 :near_match_max_m
             )
            CROSS JOIN LATERAL (SELECT ST_LineMerge(w.geom) AS line) merged
            LEFT JOIN LATERAL (
                SELECT mod(degrees(ST_Azimuth(
                           ST_ClosestPoint(ST_LineMerge(w2.geom), s.point),
                           s.point
                       ))::numeric, 360)::double precision AS roadside_bearing
                FROM weggeg_lane w2
                WHERE w2.lane = 1
                  AND w2.road_number = w.road_number
                  AND w2.carriageway_side IN ('R', 'L')
                  AND w2.carriageway_side <> w.carriageway_side
                  AND ST_DWithin(w2.geom::geography, s.point::geography, 120)
                  AND NOT ST_Equals(
                      ST_ClosestPoint(ST_LineMerge(w2.geom), s.point), s.point)
                ORDER BY w2.geom <-> s.point
                LIMIT 1
            ) opp ON true
            WHERE GeometryType(merged.line) = 'LINESTRING'
            ORDER BY s.i, distance_m
            """
        ),
        {
            "sites": json.dumps(payload),
            "near_match_max_m": WEGGEG_NEAR_MATCH_MAX_DISTANCE_M,
        },
    ).all()

    near_by_index: dict[int, list] = defaultdict(list)
    for row in near_rows:
        near_by_index[row.i].append(row)

    matched_indices: set[int] = set()
    for index, candidates in near_by_index.items():
        selected = _pick_near_candidate(candidates)
        _apply_weggeg_match(features[index], selected, "near_geometry")
        matched_indices.add(index)

    wide_payload = [site for site in payload if site["i"] not in matched_indices]
    wide_payload = [
        site
        for site in wide_payload
        if site["road_number"] is not None and site["km"] is not None
    ]
    if not wide_payload:
        return

    wide_rows = db.execute(
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
                    km double precision,
                    num_lanes integer
                )
            )
            SELECT s.i,
                   w.source_id,
                   w.lane_count,
                   w.road_number,
                   w.carriageway_side,
                   true AS road_match,
                   (w.carriageway_side = s.carriageway) AS carriageway_match,
                   (w.lane_count = s.num_lanes) AS lane_count_match,
                   ST_Distance(s.point::geography, w.geom::geography) AS distance_m,
                   mod((
                       degrees(ST_Azimuth(
                           ST_LineInterpolatePoint(
                               merged.line,
                               greatest(ST_LineLocatePoint(merged.line, s.point) - 0.0001, 0)
                           ),
                           ST_LineInterpolatePoint(
                               merged.line,
                               least(ST_LineLocatePoint(merged.line, s.point) + 0.0001, 1)
                           )
                       )) + CASE WHEN w.direction = 'T' THEN 180 ELSE 0 END
                   )::numeric, 360)::double precision AS bearing,
                   opp.roadside_bearing
            FROM sites s
            JOIN weggeg_lane w
              ON w.lane = 1
             AND w.road_number = s.road_number
             AND s.km BETWEEN
                 least((w.raw->>'BEGINKM')::double precision,
                       (w.raw->>'EINDKM')::double precision) - 0.25
                 AND
                 greatest((w.raw->>'BEGINKM')::double precision,
                          (w.raw->>'EINDKM')::double precision) + 0.25
             AND ST_DWithin(
                 w.geom::geography,
                 s.point::geography,
                 :wide_match_max_m
             )
            CROSS JOIN LATERAL (SELECT ST_LineMerge(w.geom) AS line) merged
            LEFT JOIN LATERAL (
                SELECT mod(degrees(ST_Azimuth(
                           ST_ClosestPoint(ST_LineMerge(w2.geom), s.point),
                           s.point
                       ))::numeric, 360)::double precision AS roadside_bearing
                FROM weggeg_lane w2
                WHERE w2.lane = 1
                  AND w2.road_number = w.road_number
                  AND w2.carriageway_side IN ('R', 'L')
                  AND w2.carriageway_side <> w.carriageway_side
                  AND ST_DWithin(w2.geom::geography, s.point::geography, 120)
                  AND NOT ST_Equals(
                      ST_ClosestPoint(ST_LineMerge(w2.geom), s.point), s.point)
                ORDER BY w2.geom <-> s.point
                LIMIT 1
            ) opp ON true
            WHERE GeometryType(merged.line) = 'LINESTRING'
            ORDER BY s.i, distance_m
            """
        ),
        {
            "sites": json.dumps(wide_payload),
            "wide_match_max_m": WEGGEG_WIDE_MATCH_MAX_DISTANCE_M,
        },
    ).all()

    wide_by_index: dict[int, list] = defaultdict(list)
    for row in wide_rows:
        wide_by_index[row.i].append(row)

    for index, candidates in wide_by_index.items():
        selected = _pick_wide_candidate(candidates)
        _apply_weggeg_match(features[index], selected, "road_carriageway_lanes")


def _pick_near_candidate(candidates: list):
    """Rank sub-2.5m candidates by road, carriageway, then distance."""
    return min(
        candidates,
        key=lambda candidate: (
            not bool(candidate.road_match),
            not bool(candidate.carriageway_match),
            float(candidate.distance_m),
        ),
    )


def _pick_wide_candidate(candidates: list):
    """Rank wider same-road candidates by carriageway, lanes, then distance."""
    return min(
        candidates,
        key=lambda candidate: (
            not bool(candidate.carriageway_match),
            not bool(candidate.lane_count_match),
            float(candidate.distance_m),
        ),
    )


def _apply_weggeg_match(feature: dict, selected, method: str) -> None:
    props = feature["properties"]
    props["weggeg_source_id"] = selected.source_id
    props["weggeg_lane_count"] = selected.lane_count
    props["weggeg_distance_m"] = round(float(selected.distance_m), 1)
    props["weggeg_match_method"] = method
    props["weggeg_matched_road_number"] = selected.road_number
    props["weggeg_matched_carriageway"] = selected.carriageway_side
    if selected.bearing is not None:
        props["bearing"] = round(float(selected.bearing) % 360, 1)
        props["bearing_source"] = "weggeg"
    if selected.roadside_bearing is not None:
        props["roadside_bearing"] = round(float(selected.roadside_bearing) % 360, 1)


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


def _lane_start_point(geom_json: str | None) -> list[float] | None:
    """Return the start [lon, lat] of a lane geometry (Line/MultiLine).

    Lanes are parallel offset curves of the same base line, so their *start*
    vertices are perpendicular offsets of the base start — reliably aligned
    across lanes. A mid-vertex is not (offset curves get different vertex counts
    on bends), which corrupts the cross-road projection on long/curved sections.
    """
    if not geom_json:
        return None
    geom = json.loads(geom_json)
    coords = geom.get("coordinates")
    if geom.get("type") == "MultiLineString":
        coords = coords[0] if coords else None
    if not coords:
        return None
    return coords[0]


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

    # WEGGEG lane geometry is offset assuming the source line is digitised with
    # increasing hectometres, but digitisation direction is inconsistent. When a
    # section is digitised the "wrong" way, lane 1 (fast) ends up offset to the
    # shoulder instead of the median, so speeds appear mirrored across the road.
    # The *set* of parallel lane lines is still correct — only the lane→line
    # labelling flips — so detect the mirror (using the point's roadside_bearing,
    # which points outward toward the shoulder) and reverse the speed→lane lookup.
    rows_by_source: dict[str, list] = defaultdict(list)
    for row in rows:
        rows_by_source[row.source_id].append(row)

    mirrored_sources: set[str] = set()
    for source_id, srows in rows_by_source.items():
        outward = next(
            (p["properties"].get("roadside_bearing")
             for p in matched.get(source_id, [])
             if p["properties"].get("roadside_bearing") is not None),
            None,
        )
        if outward is None or len(srows) < 2:
            continue
        ordered = sorted(srows, key=lambda r: r.lane)
        m1 = _lane_start_point(ordered[0].geom_json)
        m_last = _lane_start_point(ordered[-1].geom_json)
        if not m1 or not m_last:
            continue
        rad = math.radians(outward)
        # Project (lowest-lane → highest-lane) onto the outward direction. If the
        # highest lane is *less* outward, lane 1 sits on the shoulder → mirrored.
        proj = (m_last[0] - m1[0]) * math.sin(rad) + (m_last[1] - m1[1]) * math.cos(rad)
        if proj < 0:
            mirrored_sources.add(source_id)

    lane_features = []
    for row in rows:
        mirrored = row.source_id in mirrored_sources
        effective_lane = (
            row.lane_count + 1 - row.lane
            if mirrored and row.lane_count
            else row.lane
        )
        candidates: list[tuple[dict, dict]] = []
        for point in matched[row.source_id]:
            lane = next(
                (
                    item
                    for item in point["properties"].get("lanes", [])
                    if item["lane"] == effective_lane
                ),
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
        # Every sensor covering this lane, so the client can order them along the
        # section and fade the colour between their speeds. The winner above
        # still fills speed_kmh for labels and the single-sensor case.
        sensors = [
            {
                "site_id": pt["properties"].get("site_id"),
                "measurement_coords": pt["geometry"]["coordinates"],
                "measured_at": pt["properties"].get("measured_at"),
                "speed_kmh": ld.get("speed_kmh"),
                "flow_veh_h": ld.get("flow_veh_h"),
                "n_inputs": ld.get("n_inputs"),
                "std_dev": ld.get("std_dev"),
            }
            for pt, ld in candidates
        ]
        lane_features.append({
            "type": "Feature",
            "geometry": json.loads(row.geom_json) if row.geom_json else None,
            "properties": {
                "id": row.id,
                "source_id": row.source_id,
                # effective_lane so the shown lane number matches the speed after
                # mirror correction; the geometry stays at its physical position.
                "lane": effective_lane,
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
                "sensors": sensors,
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
    # Fetch sensors from a slightly wider area than the viewport so a sensor
    # just outside the frame can still colour a section that reaches inside it.
    # Without this, panning ~20m flips which of two co-located sensors is in the
    # bbox and the section abruptly recolours instead of showing both.
    margin = 0.004  # ≈350-500 m in NL latitudes
    fetch_bbox = BBox(
        b.min_lon - margin, b.min_lat - margin, b.max_lon + margin, b.max_lat + margin
    )
    point_response = get_speed(b=fetch_bbox, db=db, limit=limit)
    points = json.loads(point_response.body)
    lanes = (
        _lane_speed_feature_collection(db, points["features"], b)
        if include_lanes
        else {"type": "FeatureCollection", "features": []}
    )
    # Clip point markers back to the requested viewport; the wider fetch exists
    # only to feed lane matching, not to draw sensors outside the frame.
    points["features"] = [
        f
        for f in points["features"]
        if f.get("geometry")
        and b.min_lon <= f["geometry"]["coordinates"][0] <= b.max_lon
        and b.min_lat <= f["geometry"]["coordinates"][1] <= b.max_lat
    ]
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
