"""PostGIS candidate retrieval for the Matrix dry-run matcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ndwinfo.road_matching.types import LaneCandidate, MatrixSign


@dataclass(frozen=True)
class MatrixCandidateSnapshot:
    signs: tuple[MatrixSign, ...]
    candidates_by_uuid: dict[str, tuple[LaneCandidate, ...]]
    raw_source_count: int
    deduped_source_count: int


_MATRIX_CANDIDATE_SQL = text(
    """
    WITH raw_source AS (
        SELECT
            s.uuid, s.road, s.carriageway, s.lane, s.km, s.bearing,
            s.geom,
            round(s.km::numeric, 2) AS km_bucket,
            st.ts_state,
            row_number() OVER (
                PARTITION BY s.road, s.carriageway, s.lane, round(s.km::numeric, 2)
                ORDER BY st.ts_state DESC NULLS LAST, s.uuid
            ) AS ghost_rank
        FROM msi_sign s
        LEFT JOIN msi_state st ON st.uuid = s.uuid
        WHERE s.geom IS NOT NULL
          AND (:min_lon IS NULL OR ST_Intersects(
              s.geom, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
          ))
    ), deduped_source AS (
        SELECT * FROM raw_source WHERE ghost_rank = 1
    ), selected_gantries AS (
        SELECT road, carriageway, km_bucket
        FROM deduped_source
        GROUP BY road, carriageway, km_bucket
        ORDER BY road, carriageway, km_bucket
        LIMIT :source_limit
    ), selected_source AS (
        SELECT d.*
        FROM deduped_source d
        JOIN selected_gantries g
          ON g.road IS NOT DISTINCT FROM d.road
         AND g.carriageway IS NOT DISTINCT FROM d.carriageway
         AND g.km_bucket IS NOT DISTINCT FROM d.km_bucket
    )
    SELECT
        s.uuid, s.road, s.carriageway, s.lane, s.km, s.bearing,
        s.ts_state,
        ST_X(s.geom) AS lon, ST_Y(s.geom) AS lat,
        c.lane_id, c.road_id, c.segment_id, c.direction, c.lane_nr,
        c.lane_count, c.ref, c.highway, c.distance_m, c.bearing_deg,
        c.position_fraction, c.projected_wkt, c.carriageway_ref
    FROM selected_source s
    LEFT JOIN LATERAL (
        SELECT
            l.id AS lane_id,
            l.road_id,
            l.segment_id,
            l.direction,
            l.lane_nr,
            l.lane_count,
            r.ref,
            r.highway,
            coalesce(r.raw->>'carriageway_ref', r.raw->>'carriageway:ref') AS carriageway_ref,
            ST_Distance(s.geom::geography, l.geom::geography) AS distance_m,
            mod((
                degrees(ST_Azimuth(
                    ST_LineInterpolatePoint(l.geom, greatest(
                        ST_LineLocatePoint(l.geom, s.geom) - 0.001, 0
                    )),
                    ST_LineInterpolatePoint(l.geom, least(
                        ST_LineLocatePoint(l.geom, s.geom) + 0.001, 1
                    ))
                )) + 360
            )::numeric, 360)::double precision AS bearing_deg,
            ST_LineLocatePoint(l.geom, s.geom) AS position_fraction,
            ST_AsText(ST_ClosestPoint(l.geom, s.geom)) AS projected_wkt
        FROM osm_lane_centerline l
        JOIN osm_road r ON r.osm_id = l.road_id
        WHERE l.direction IN ('fwd', 'bwd')
          AND ST_DWithin(s.geom::geography, l.geom::geography, :radius_m)
        ORDER BY l.geom::geography <-> s.geom::geography, l.id
        LIMIT :candidate_limit
    ) c ON TRUE
    ORDER BY s.uuid, c.distance_m NULLS LAST, c.lane_id
    """
)

_MATRIX_PROFILE_SQL = text(
    """
    WITH source AS (
        SELECT s.road, s.carriageway, s.lane, round(s.km::numeric, 2) AS km_bucket
        FROM msi_sign s
        WHERE s.geom IS NOT NULL
          AND (:min_lon IS NULL OR ST_Intersects(
              s.geom, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
          ))
    )
    SELECT
        count(*) AS raw_source_count,
        (SELECT count(*) FROM (
            SELECT DISTINCT road, carriageway, lane, km_bucket FROM source
        ) slots)
            AS deduped_source_count
    FROM source
    """
)


def _parse_wkt_point(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    coords = value[value.find("(") + 1 : value.rfind(")")].split()
    return (float(coords[0]), float(coords[1])) if len(coords) == 2 else None


def _timestamp(value: Any):
    return value.isoformat() if hasattr(value, "isoformat") else value


def load_matrix_candidates(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None,
    source_limit: int = 500,
    radius_m: float = 20.0,
    candidate_limit: int = 64,
) -> MatrixCandidateSnapshot:
    """Load a bounded, ghost-deduplicated Matrix snapshot plus OSM candidates."""

    if source_limit < 1 or candidate_limit < 1 or radius_m <= 0:
        raise ValueError(
            "source_limit/candidate_limit must be positive and radius_m must be positive"
        )
    params: dict[str, Any] = {
        "min_lon": bbox[0] if bbox else None,
        "min_lat": bbox[1] if bbox else None,
        "max_lon": bbox[2] if bbox else None,
        "max_lat": bbox[3] if bbox else None,
        "source_limit": source_limit,
        "radius_m": radius_m,
        "candidate_limit": candidate_limit,
    }
    profile = session.execute(_MATRIX_PROFILE_SQL, params).mappings().one()
    rows = session.execute(_MATRIX_CANDIDATE_SQL, params).mappings().all()
    signs_by_uuid: dict[str, MatrixSign] = {}
    candidates: dict[str, list[LaneCandidate]] = {}
    for row in rows:
        uuid = row["uuid"]
        signs_by_uuid.setdefault(
            uuid,
            MatrixSign(
                uuid=uuid,
                road=row["road"],
                carriageway=row["carriageway"],
                lane=row["lane"],
                km=float(row["km"]) if row["km"] is not None else None,
                bearing=float(row["bearing"]) if row["bearing"] is not None else None,
                state_timestamp=_timestamp(row["ts_state"]),
                lon=float(row["lon"]) if row["lon"] is not None else None,
                lat=float(row["lat"]) if row["lat"] is not None else None,
            ),
        )
        if row["lane_id"] is None:
            continue
        candidates.setdefault(uuid, []).append(
            LaneCandidate(
                lane_id=row["lane_id"],
                road_id=int(row["road_id"]),
                segment_id=row["segment_id"],
                direction=row["direction"],
                lane_nr=int(row["lane_nr"]),
                lane_count=int(row["lane_count"]),
                ref=row["ref"],
                highway=row["highway"],
                distance_m=float(row["distance_m"]),
                bearing_deg=float(row["bearing_deg"]) if row["bearing_deg"] is not None else None,
                position_fraction=(
                    float(row["position_fraction"])
                    if row["position_fraction"] is not None
                    else None
                ),
                projected=_parse_wkt_point(row["projected_wkt"]),
                carriageway_ref=row["carriageway_ref"],
            )
        )
    signs = tuple(sorted(signs_by_uuid.values(), key=lambda item: item.uuid))
    return MatrixCandidateSnapshot(
        signs=signs,
        candidates_by_uuid={key: tuple(value) for key, value in candidates.items()},
        raw_source_count=int(profile["raw_source_count"] or 0),
        deduped_source_count=int(profile["deduped_source_count"] or 0),
    )
