"""Persistence for explainable source-point to OSM road assignments."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Iterable

from geoalchemy2.elements import WKTElement
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ndwinfo.models import RoadPointAssignment, RoadPointLink
from ndwinfo.road_matching.types import MatrixSign, MatrixSignMatch

MATRIX_SOURCE_KIND = "matrix"


def matrix_source_fingerprint(sign: MatrixSign) -> str:
    """Return a stable fingerprint for the source evidence used by matching."""

    payload = {
        "uuid": sign.uuid,
        "road": sign.road,
        "carriageway": sign.carriageway,
        "lane": sign.lane,
        "km": sign.km,
        "bearing": sign.bearing,
        "lon": sign.lon,
        "lat": sign.lat,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def persist_matrix_matches(
    session: Session,
    signs: Iterable[MatrixSign],
    matches: Iterable[MatrixSignMatch],
    *,
    algorithm_version: str,
) -> int:
    """Replace the selected Matrix assignment snapshot in one transaction.

    Every source row gets an assignment, including ambiguous/unmatched rows.
    Only a successful match gets a point link, making a missing link an
    explicit, queryable outcome rather than stale data from an earlier run.

    A run also drops every Matrix assignment written by a *different*
    ``algorithm_version``. Those rows were produced by superseded matching
    logic, so leaving them in place would let the API keep serving decisions
    the current matcher would not make -- including ones it now rejects. Rows
    written by this same version outside the current area are left alone, so
    bounded per-area runs still accumulate.
    """

    signs_by_uuid = {sign.uuid: sign for sign in signs}
    match_rows = list(matches)
    source_keys = [match.uuid for match in match_rows]
    if not source_keys:
        return 0

    # Links cascade with their assignment; this covers the rows being rewritten.
    session.execute(
        delete(RoadPointLink).where(
            RoadPointLink.source_kind == MATRIX_SOURCE_KIND,
            RoadPointLink.source_key.in_(source_keys),
        )
    )
    session.execute(
        delete(RoadPointAssignment).where(
            RoadPointAssignment.source_kind == MATRIX_SOURCE_KIND,
            RoadPointAssignment.algorithm_version != algorithm_version,
        )
    )

    now = datetime.now(UTC)
    assignments = []
    links = []
    for match in match_rows:
        sign = signs_by_uuid.get(match.uuid)
        if sign is None:
            raise ValueError(f"match has no source sign: {match.uuid}")
        assignments.append(
            {
                "source_kind": MATRIX_SOURCE_KIND,
                "source_key": match.uuid,
                "status": match.status,
                "confidence": match.confidence,
                "method": match.method,
                "failure_reason": match.failure_reason,
                "candidate_count": match.candidate_count,
                "source_fingerprint": matrix_source_fingerprint(sign),
                "algorithm_version": algorithm_version,
                "matched_at": now,
                "diagnostics": match.diagnostics,
            }
        )
        if match.status != "matched" or match.road_id is None:
            continue
        point = None
        if match.matched_point is not None:
            point = WKTElement(
                f"POINT({match.matched_point[0]} {match.matched_point[1]})",
                srid=4326,
            )
        links.append(
            {
                "source_kind": MATRIX_SOURCE_KIND,
                "source_key": match.uuid,
                "link_index": 0,
                "road_id": match.road_id,
                "road_revision": None,
                "segment_id": match.segment_id,
                "direction": match.direction,
                "anchor_lane_id": match.anchor_lane_id,
                "applies_to_lane_id": match.applies_to_lane_id,
                "position_fraction": match.position_fraction,
                "matched_geom": point,
                "source_distance_m": match.source_distance_m,
                "bearing_error_deg": match.bearing_error_deg,
                "road_ref_quality": match.road_ref_quality,
                "confidence": match.confidence,
            }
        )

    assignment_insert = insert(RoadPointAssignment).values(assignments)
    session.execute(
        assignment_insert.on_conflict_do_update(
            index_elements=["source_kind", "source_key"],
            set_={
                column: getattr(assignment_insert.excluded, column)
                for column in (
                    "status",
                    "confidence",
                    "method",
                    "failure_reason",
                    "candidate_count",
                    "source_fingerprint",
                    "algorithm_version",
                    "matched_at",
                    "diagnostics",
                )
            },
        )
    )
    if links:
        session.execute(insert(RoadPointLink).values(links))
    return len(assignments)
