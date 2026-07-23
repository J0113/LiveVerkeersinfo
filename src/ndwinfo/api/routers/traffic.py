"""Traffic speed and travel-time endpoints."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import ColumnElement, and_, case, func, select, text, tuple_

from ndwinfo.api.deps import BBox, BBoxDep, DbDep, OptionalBBoxDep
from ndwinfo.api.geo import geo_response, make_fc
from ndwinfo.config import settings
from ndwinfo.models import (
    MeasurementCharacteristic,
    MeasurementSite,
    OsmRoad,
    OsmRoadLane,
    TrafficMeasurement,
    TravelTime,
    VildTmc,
)
from ndwinfo.osm_tags import osm_maxspeed_kmh as _osm_maxspeed_kmh

router = APIRouter(prefix="/traffic", tags=["traffic"])

OSM_MATCH_MAX_DISTANCE_M = 25
OSM_MATCH_MAX_ANGLE_DEG = 45
OSM_CLOSE_MATCH_MAX_DISTANCE_M = 5
OSM_CLOSE_MATCH_MAX_ANGLE_DEG = 15


def _speed_location_key(
    coords: tuple,
    road: str | None,
    carriageway: str | None,
    tmc_direction: str | None,
    side: str | None,
) -> tuple:
    """Keep co-located opposite directions separate when explicit R/L is absent."""
    return (coords, road, carriageway or tmc_direction, side)


@dataclass
class _SpeedScope:
    """WHERE-clause + ordering for one speed-feature query: bbox, road, or both."""

    predicates: list[ColumnElement]
    order_by_km: bool = False


def _normalize_carriageway(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in {"R", "L"}:
        raise HTTPException(400, "carriageway: expected 'R' or 'L'")
    return normalized


def _infer_carriageway(
    db: DbDep, road: str, lon: float, lat: float, heading: float | None
) -> str | None:
    """Nearest effective_carriageway for `road` at (lon, lat), direction-checked.

    Opposite carriageways are physically separated (a median, or simply two
    distinct roadways), so nearest-by-distance is already a strong signal.
    When a heading is given and a candidate has a known VILD travel bearing,
    prefer the first candidate whose bearing roughly agrees with it.
    """
    rows = db.execute(
        text(
            """
            SELECT effective_carriageway, vild_bearing
            FROM measurement_site
            WHERE effective_road = :road
              AND effective_carriageway IS NOT NULL
              AND geom IS NOT NULL
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
            LIMIT 5
            """
        ),
        {"road": road, "lon": lon, "lat": lat},
    ).all()
    if not rows:
        return None
    if heading is not None:
        for row in rows:
            if row.vild_bearing is None:
                continue
            if _angular_difference(float(row.vild_bearing), heading) <= 90:
                return row.effective_carriageway
    return rows[0].effective_carriageway


def _build_speed_features(db: DbDep, scope: _SpeedScope, limit: int) -> tuple[list[dict], int]:
    """Query + merge speed features for `scope`. Returns (features, total_matched).

    `total_matched` is the location count before the `limit` slice, so callers
    can report truncation instead of silently dropping matches.

    Filters to anyVehicle aggregate measurements (no vehicle-length constraint)
    so each lane yields exactly one speed and one flow value.

    Multiple measurement systems (MONIBAS aggregate, MONICA per-lane, regional
    nets) can sit at the same point. We merge them **per lane**: readings sharing
    the latest timestamp are averaged; otherwise the latest non-null/non-zero
    reading wins. One marker per (location, side) instead of stacked duplicates.
    """
    # Bound candidate sites *before* the join/aggregation below so `limit`
    # constrains DB/CPU/JSON work, not just the Python-side feature count.
    # Must apply the same usability filters as the main query (lane present,
    # no vehicle-length constraint) rather than just scope membership: most
    # sites in this dataset have no usable lane reading (filtered out below),
    # and bounding on scope-membership alone risks the whole candidate window
    # filling with unusable sites, silently emptying the result for a large
    # scope with a comparatively small limit. 6x margin keeps co-located
    # opposite-direction site pairs (2 distinct MeasurementSite rows sharing a
    # physical location) together.
    order_cols = (
        (MeasurementSite.km.asc().nulls_last(), TrafficMeasurement.site_id)
        if scope.order_by_km
        else (TrafficMeasurement.site_id,)
    )
    # GROUP BY (site_id, km) instead of SELECT DISTINCT site_id: Postgres
    # requires DISTINCT's ORDER BY expressions to appear in the select list,
    # which would force km into the scalar subquery's single output column.
    # km is single-valued per site_id, so grouping on both changes nothing
    # about which/how-many site_ids come out, only lets ORDER BY use km.
    candidate_site_ids = (
        select(TrafficMeasurement.site_id)
        .group_by(TrafficMeasurement.site_id, MeasurementSite.km)
        .join(MeasurementSite, TrafficMeasurement.site_id == MeasurementSite.id)
        .join(
            MeasurementCharacteristic,
            and_(
                TrafficMeasurement.site_id == MeasurementCharacteristic.site_id,
                TrafficMeasurement.index == MeasurementCharacteristic.index,
            ),
        )
        .where(
            *scope.predicates,
            MeasurementCharacteristic.lane.isnot(None),
            MeasurementCharacteristic.veh_length_min.is_(None),
            MeasurementCharacteristic.veh_length_max.is_(None),
        )
        .order_by(*order_cols)
        .limit(limit * 6)
        .scalar_subquery()
    )

    rows = db.execute(
        select(
            TrafficMeasurement.site_id,
            MeasurementSite.num_lanes,
            MeasurementSite.side,
            MeasurementSite.road,
            MeasurementSite.carriageway,
            MeasurementSite.carriageway_source,
            MeasurementSite.vild_carriageway,
            MeasurementSite.vild_carriageway_source,
            MeasurementSite.carriageway_direction_conflict,
            MeasurementSite.km,
            MeasurementSite.openlr_bearing,
            MeasurementSite.vild_bearing,
            MeasurementSite.tmc_direction,
            VildTmc.road_number.label("vild_road_number"),
            VildTmc.hecto_dir.label("vild_hecto_dir"),
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
        .outerjoin(VildTmc, MeasurementSite.tmc_primary == VildTmc.loc_nr)
        .where(
            TrafficMeasurement.site_id.in_(candidate_site_ids),
            *scope.predicates,
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
            MeasurementSite.carriageway_source,
            MeasurementSite.vild_carriageway,
            MeasurementSite.vild_carriageway_source,
            MeasurementSite.carriageway_direction_conflict,
            MeasurementSite.km,
            MeasurementSite.openlr_bearing,
            MeasurementSite.vild_bearing,
            MeasurementSite.tmc_direction,
            VildTmc.road_number,
            VildTmc.hecto_dir,
            MeasurementCharacteristic.lane,
            MeasurementSite.geom,
        )
        .order_by(*order_cols, MeasurementCharacteristic.lane)
    ).all()

    # Identify road/carriageway metadata available at each physical point first.
    # Some MONICA records omit road/km but are exactly co-located with a MONIBAS
    # record that has them. Inherit only when there is one unambiguous candidate.
    known_at_position: dict[
        tuple, set[tuple[str, str | None, str | None]]
    ] = defaultdict(set)
    for row in rows:
        if not row.geom_json or not row.road:
            continue
        row_coords = tuple(round(c, 5) for c in json.loads(row.geom_json)["coordinates"])
        known_at_position[(row_coords, row.side, row.tmc_direction)].add(
            (row.road, row.carriageway, row.carriageway_source)
        )

    # Bucket lane readings by physical location + road direction. Location is
    # rounded to ~1m so co-located systems merge, while carriageways stay apart.
    locs: dict[tuple, dict] = {}
    for r in rows:
        if not r.geom_json:
            continue
        coords = tuple(round(c, 5) for c in json.loads(r.geom_json)["coordinates"])
        effective_road = r.road or r.vild_road_number
        effective_carriageway = r.carriageway
        effective_carriageway_source = r.carriageway_source
        inherited = known_at_position.get((coords, r.side, r.tmc_direction), set())
        if effective_road is None and len(inherited) == 1:
            effective_road, effective_carriageway, effective_carriageway_source = next(
                iter(inherited)
            )
        # Keep opposite carriageways separate even when two systems publish the
        # same gantry coordinate and measurementSide is absent.
        key = _speed_location_key(
            coords,
            effective_road,
            effective_carriageway,
            r.tmc_direction,
            r.side,
        )
        loc = locs.get(key)
        if loc is None:
            loc = locs[key] = {
                "coords": coords,
                "side": r.side,
                "road": effective_road,
                "carriageway": effective_carriageway,
                "carriageway_source": effective_carriageway_source,
                "derived_carriageway": r.vild_carriageway,
                "derived_carriageway_source": r.vild_carriageway_source,
                "carriageway_direction_conflict": r.carriageway_direction_conflict,
                "km": float(r.km) if r.km is not None else None,
                "openlr_bearing": int(r.openlr_bearing) if r.openlr_bearing is not None else None,
                "bearing": float(r.vild_bearing) if r.vild_bearing is not None else None,
                "bearing_source": "vild" if r.vild_bearing is not None else None,
                "tmc_direction": r.tmc_direction,
                "vild_hecto_dir": r.vild_hecto_dir,
                "num_lanes": r.num_lanes or 0,
                "sources": set(),
                "lanes": defaultdict(list),  # lane -> list of readings
            }
        loc["num_lanes"] = max(loc["num_lanes"], r.num_lanes or 0)
        if r.road is not None and (loc["road"] is None or loc["km"] is None):
            loc["road"] = r.road
            loc["carriageway"] = r.carriageway
            loc["carriageway_source"] = r.carriageway_source
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
                "carriageway_source": loc["carriageway_source"],
                "derived_carriageway": loc["derived_carriageway"],
                "derived_carriageway_source": loc["derived_carriageway_source"],
                "carriageway_direction_conflict": loc["carriageway_direction_conflict"],
                "km": loc["km"],
                "openlr_bearing": loc["openlr_bearing"],
                "bearing": loc["bearing"],
                "bearing_source": loc["bearing_source"],
                "tmc_direction": loc["tmc_direction"],
                "vild_hecto_dir": loc["vild_hecto_dir"],
                "num_lanes": loc["num_lanes"] or None,
                "side": loc["side"],
                "measured_at": feat_ts.isoformat() if feat_ts else None,
                "systems": systems,
                "source_count": len(sources),
                "lanes": lanes_out,
            },
        })

    _attach_osm_matches(db, features)
    _attach_fallback_bearings(db, features)
    return features, len(locs)


@router.get("/speed")
def get_speed(
    db: DbDep,
    b: OptionalBBoxDep = None,
    road: str | None = Query(None, description="Road number, e.g. A2 — normalized (a02 == A2)"),
    carriageway: str | None = Query(
        None, description="R or L; auto-detected from lon/lat/heading if omitted"
    ),
    km_min: float | None = None,
    km_max: float | None = None,
    lon: float | None = Query(None, description="With lat/heading, infers carriageway if omitted"),
    lat: float | None = None,
    heading: float | None = Query(None, ge=0, lt=360),
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    """Return one GeoJSON feature per location with merged per-lane speed/flow.

    Scope is `bbox`, `road` (+ optional `carriageway`/`km_min`/`km_max`), or
    both combined — at least one is required so this never returns the
    unfiltered national set. `road` matches the ingest-resolved
    `effective_road`/`effective_carriageway` (explicit, else VILD-derived,
    else inherited from a co-located sibling site), not the raw columns.
    """
    if b is None and road is None:
        raise HTTPException(400, "bbox or road is required")

    predicates: list[ColumnElement] = []
    if b is not None:
        bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
        predicates.append(func.ST_Intersects(MeasurementSite.geom, bbox_geom))

    road_norm = None
    if road is not None:
        road_norm = _normalized_road_ref(road)
        if road_norm is None:
            raise HTTPException(400, "road: not a recognizable road reference")
        predicates.append(MeasurementSite.effective_road == road_norm)

        cw = _normalize_carriageway(carriageway)
        if cw is None and lon is not None and lat is not None:
            cw = _infer_carriageway(db, road_norm, lon, lat, heading)
        if cw is not None:
            predicates.append(MeasurementSite.effective_carriageway == cw)

        if km_min is not None:
            predicates.append(MeasurementSite.km >= km_min)
        if km_max is not None:
            predicates.append(MeasurementSite.km <= km_max)

    scope = _SpeedScope(predicates=predicates, order_by_km=road is not None)
    features, total_matched = _build_speed_features(db, scope, limit)
    return geo_response({
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "truncated": total_matched > limit,
    })


def _normalized_road_ref(value: str | None) -> str | None:
    """Normalize OSM/VILD/measurement road references for comparison."""
    refs = _normalized_road_refs(value)
    return next(iter(refs), None)


def _normalized_road_refs(value: str | None) -> tuple[str, ...]:
    """Normalize every road reference in values such as ``A9;A200``."""
    if not value:
        return ()
    refs = []
    for prefix, number, suffix in re.findall(
        r"(?<![A-Z0-9])([AN])?\s*0*(\d+)([A-Z]?)(?![A-Z0-9])",
        value.upper(),
    ):
        normalized = f"{prefix or ''}{int(number)}{suffix or ''}"
        if normalized not in refs:
            refs.append(normalized)
    return tuple(refs)


def _road_ref_quality(site_ref: str | None, candidate_ref: str | None) -> int:
    """Rank exact refs above same-number A/N transitions.

    Returns 2 for an exact reference, 1 for the same numbered corridor with a
    different or absent A/N prefix, 0 when either side has no usable reference,
    and -1 for a genuinely different road number.
    """
    site_refs = _normalized_road_refs(site_ref)
    candidate_refs = _normalized_road_refs(candidate_ref)
    if not site_refs or not candidate_refs:
        return 0
    if set(site_refs) & set(candidate_refs):
        return 2

    def corridor(ref: str) -> str:
        return ref[1:] if ref[:1] in {"A", "N"} else ref

    if {corridor(ref) for ref in site_refs} & {corridor(ref) for ref in candidate_refs}:
        return 1
    return -1


def _angular_difference(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _pick_osm_candidate(site: dict, candidates: list):
    """Choose a confidently directed OSM cross-section, or return ``None``."""
    road_ref = _normalized_road_ref(site.get("road_ref"))
    bearing = site.get("bearing")
    if bearing is None:
        return None

    eligible = []
    for candidate in candidates:
        ref_quality = _road_ref_quality(road_ref, candidate.ref)
        if ref_quality < 0:
            continue
        angle = _angular_difference(float(candidate.bearing), float(bearing))
        if angle > OSM_MATCH_MAX_ANGLE_DEG:
            continue
        close_reference_match = (
            ref_quality >= 1
            and float(candidate.distance_m) < OSM_CLOSE_MATCH_MAX_DISTANCE_M
            and angle <= OSM_CLOSE_MATCH_MAX_ANGLE_DEG
        )
        eligible.append(
            (
                candidate,
                close_reference_match,
                ref_quality,
                bool(site.get("num_lanes") and candidate.lane_count == site["num_lanes"]),
                angle,
            )
        )
    if not eligible:
        return None

    eligible.sort(
        key=lambda item: (
            not item[1],
            -item[2],
            not item[3],
            item[4],
            float(item[0].distance_m),
        )
    )
    best = eligible[0]
    if len(eligible) > 1:
        second = eligible[1]
        indistinguishable = (
            best[0].source_id != second[0].source_id
            and second[0].source_id
            not in (getattr(best[0], "connected_source_ids", None) or [])
            and best[0].source_id
            not in (getattr(second[0], "connected_source_ids", None) or [])
            and best[1:4] == second[1:4]
            and abs(best[4] - second[4]) <= 2
            and abs(float(best[0].distance_m) - float(second[0].distance_m)) <= 1
        )
        if indistinguishable:
            return None
    return best[0]


def _osm_failure_reason(site: dict, candidates: list) -> str:
    """Explain why a site with nearby OSM geometry was not matched."""
    road_ref = _normalized_road_ref(site.get("road_ref"))
    road_compatible = [
        candidate
        for candidate in candidates
        if _road_ref_quality(road_ref, candidate.ref) >= 0
    ]
    if not road_compatible:
        return "road_ref_conflict"
    bearing = site.get("bearing")
    directed = [
        candidate
        for candidate in road_compatible
        if bearing is not None
        and _angular_difference(float(candidate.bearing), float(bearing))
        <= OSM_MATCH_MAX_ANGLE_DEG
    ]
    return "ambiguous_candidates" if directed else "bearing_mismatch"


def _attach_osm_matches(db, features: list[dict]) -> None:
    """Match directed speed sites to nearby OSM directional lane geometry."""
    payload = []
    for index, feature in enumerate(features):
        props = feature["properties"]
        if props.get("bearing") is None:
            continue
        lon, lat = feature["geometry"]["coordinates"]
        payload.append({
            "i": index,
            "lon": lon,
            "lat": lat,
            "road_ref": props.get("road"),
            "bearing": props.get("bearing"),
            "num_lanes": props.get("num_lanes"),
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
                    road_ref text,
                    bearing double precision,
                    num_lanes integer
                )
            )
            SELECT s.i,
                   o.source_id,
                   o.lane_count,
                   o.ref,
                   o.name,
                   o.direction,
                   o.highway,
                   r.raw AS osm_tags,
                   ARRAY(
                       SELECT o2.source_id
                       FROM osm_road_lane o2
                       WHERE o2.lane = 1
                         AND o2.source_id <> o.source_id
                         AND ST_DWithin(
                             o2.geom::geography,
                             o.geom::geography,
                             1
                         )
                   ) AS connected_source_ids,
                   ST_Distance(s.point::geography, o.geom::geography) AS distance_m,
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
                       )) + CASE WHEN o.direction = 'bwd' THEN 180 ELSE 0 END
                   )::numeric, 360)::double precision AS bearing
            FROM sites s
            JOIN osm_road_lane o
              ON o.lane = 1
             AND o.direction IN ('fwd', 'bwd')
             AND coalesce(o.role, '') <> 'connector'
             AND ST_DWithin(
                 o.geom::geography,
                 s.point::geography,
                 :max_distance_m
             )
            JOIN osm_road r ON r.osm_id = o.source_id
            CROSS JOIN LATERAL (SELECT ST_LineMerge(o.geom) AS line) merged
            WHERE GeometryType(merged.line) = 'LINESTRING'
            ORDER BY s.i, distance_m
            """
        ),
        {"sites": json.dumps(payload), "max_distance_m": OSM_MATCH_MAX_DISTANCE_M},
    ).all()

    by_index: dict[int, list] = defaultdict(list)
    for row in rows:
        by_index[row.i].append(row)
    sites = {site["i"]: site for site in payload}
    for site in payload:
        features[site["i"]]["properties"]["osm_match_failure"] = "no_nearby_major_lane"
    for index, candidates in by_index.items():
        selected = _pick_osm_candidate(sites[index], candidates)
        if selected is None:
            props = features[index]["properties"]
            props["osm_match_failure"] = _osm_failure_reason(sites[index], candidates)
            props["osm_nearest_highway"] = candidates[0].highway
            continue
        props = features[index]["properties"]
        props.pop("osm_match_failure", None)
        props["osm_source_id"] = selected.source_id
        props["osm_direction"] = selected.direction
        props["osm_lane_count"] = selected.lane_count
        props["osm_distance_m"] = round(float(selected.distance_m), 1)
        props["osm_match_method"] = "vild_bearing"
        props["osm_highway"] = selected.highway
        props["osm_bearing"] = round(float(selected.bearing) % 360, 1)
        # Carried directly on the point so a road-scoped result (no matching
        # local lane fetch) can still render road name/speed limit — see
        # enrichLaneSpeedSelection in lib.js, which prefers these when present.
        props["osm_ref"] = selected.ref
        props["osm_name"] = selected.name
        props["maxspeed_kmh"] = _osm_maxspeed_kmh(selected.osm_tags, selected.direction)


def _attach_fallback_bearings(db, features: list[dict]) -> None:
    """Use OpenLR/nearest-line only when VILD bearing enrichment is unavailable.

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


def _osm_lane_speed_feature_collection(db, point_features: list[dict], b: BBoxDep) -> dict:
    """Project matched point measurements onto their directed OSM lanes."""
    matched: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for feature in point_features:
        props = feature["properties"]
        source_id = props.get("osm_source_id")
        direction = props.get("osm_direction")
        if source_id is not None and direction in {"fwd", "bwd"}:
            matched[(source_id, direction)].append(feature)
    if not matched:
        return {"type": "FeatureCollection", "features": []}

    bbox_geom = func.ST_MakeEnvelope(b.min_lon, b.min_lat, b.max_lon, b.max_lat, 4326)
    rows = db.execute(
        select(
            OsmRoadLane.id,
            OsmRoadLane.source_id,
            OsmRoadLane.lane,
            OsmRoadLane.lane_count,
            OsmRoadLane.direction,
            OsmRoadLane.highway,
            OsmRoadLane.name,
            OsmRoadLane.ref,
            OsmRoadLane.width_m,
            OsmRoad.raw.label("osm_tags"),
            func.ST_AsGeoJSON(OsmRoadLane.geom, 6).label("geom_json"),
        )
        .join(OsmRoad, OsmRoad.osm_id == OsmRoadLane.source_id)
        .where(
            tuple_(OsmRoadLane.source_id, OsmRoadLane.direction).in_(list(matched)),
            OsmRoadLane.role != "connector",
            func.ST_Intersects(OsmRoadLane.geom, bbox_geom),
        )
        .order_by(OsmRoadLane.source_id, OsmRoadLane.direction, OsmRoadLane.lane)
        .limit(settings.api_max_limit)
    ).all()

    lane_features = []
    for row in rows:
        pair = (row.source_id, row.direction)
        points = matched[pair]
        effective_lane = _effective_osm_lane(row.lane, row.lane_count, row.direction)
        candidates: list[tuple[dict, dict]] = []
        for point in points:
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
        candidates.sort(
            key=lambda item: (
                item[1].get("speed_kmh") is not None,
                item[0]["properties"].get("measured_at") or "",
                -float(item[0]["properties"].get("osm_distance_m") or 9999),
            ),
            reverse=True,
        )
        point, lane_data = candidates[0] if candidates else (points[0], {})
        # Missing readings remain available through Traffic Speed Points, but
        # must not paint an apparently measured lane section.
        if lane_data.get("speed_kmh") is None:
            continue
        point_props = point["properties"]
        sensors = [
            {
                "site_id": candidate["properties"].get("site_id"),
                "measurement_coords": candidate["geometry"]["coordinates"],
                "measured_at": candidate["properties"].get("measured_at"),
                "speed_kmh": lane.get("speed_kmh"),
                "flow_veh_h": lane.get("flow_veh_h"),
                "n_inputs": lane.get("n_inputs"),
                "std_dev": lane.get("std_dev"),
            }
            for candidate, lane in candidates
        ]
        lane_features.append({
            "type": "Feature",
            "geometry": json.loads(row.geom_json) if row.geom_json else None,
            "properties": {
                "id": row.id,
                "osm_source_id": row.source_id,
                "osm_direction": row.direction,
                "osm_lane_count": row.lane_count,
                "lane": effective_lane,
                "lane_count": row.lane_count,
                "highway": row.highway,
                "name": row.name,
                "ref": row.ref,
                "width_m": float(row.width_m) if row.width_m is not None else None,
                "maxspeed_kmh": _osm_maxspeed_kmh(row.osm_tags, row.direction),
                "road": point_props.get("road"),
                "carriageway": point_props.get("carriageway"),
                "derived_carriageway": point_props.get("derived_carriageway"),
                "tmc_direction": point_props.get("tmc_direction"),
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


def _effective_osm_lane(lane: int, lane_count: int, direction: str) -> int:
    """Translate physical OSM ordering to driver-left NDW lane numbering."""
    return lane_count + 1 - lane if direction == "bwd" else lane


@router.get("/speed/map")
def get_speed_map(
    b: BBoxDep,
    db: DbDep,
    include_lanes: bool = True,
    limit: Annotated[int, Query(ge=1, le=settings.api_max_limit)] = settings.api_default_limit,
):
    """Return confidently matched OSM speed lanes plus point fallbacks."""
    # Fetch sensors from a slightly wider area than the viewport so a sensor
    # just outside the frame can still colour a section that reaches inside it.
    # Without this, panning ~20m flips which of two co-located sensors is in the
    # bbox and the section abruptly recolours instead of showing both.
    margin = 0.004  # ≈350-500 m in NL latitudes
    fetch_bbox = BBox(
        b.min_lon - margin, b.min_lat - margin, b.max_lon + margin, b.max_lat + margin
    )
    fetch_bbox_geom = func.ST_MakeEnvelope(
        fetch_bbox.min_lon, fetch_bbox.min_lat, fetch_bbox.max_lon, fetch_bbox.max_lat, 4326
    )
    scope = _SpeedScope(predicates=[func.ST_Intersects(MeasurementSite.geom, fetch_bbox_geom)])
    point_features, _total_matched = _build_speed_features(db, scope, limit)
    lanes = (
        _osm_lane_speed_feature_collection(db, point_features, b)
        if include_lanes
        else {"type": "FeatureCollection", "features": []}
    )
    # Clip point markers back to the requested viewport; the wider fetch exists
    # only to feed lane matching, not to draw sensors outside the frame.
    point_features = [
        f
        for f in point_features
        if f.get("geometry")
        and b.min_lon <= f["geometry"]["coordinates"][0] <= b.max_lon
        and b.min_lat <= f["geometry"]["coordinates"][1] <= b.max_lat
    ]
    points = {"type": "FeatureCollection", "features": point_features}
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
