"""Run a bounded Matrix-to-OSM match, optionally persisting assignments.

Example (small local area):

    python -m ndwinfo.match_matrix --bbox 4.6,52.3,4.9,52.6 --limit 250
    python -m ndwinfo.match_matrix --bbox 4.6,52.3,4.9,52.6 --limit 250 --persist

The command emits a compact report.  ``--include-results`` adds the individual
UUID diagnostics for fixture review; no report is written to the repository by
default.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from typing import Any

from ndwinfo.db import SessionLocal
from ndwinfo.road_matching.candidates import load_matrix_candidates
from ndwinfo.road_matching.persistence import persist_matrix_matches
from ndwinfo.road_matching.points import group_matrix_signs, match_matrix_gantry
from ndwinfo.road_matching.types import MatrixSignMatch

ALGORITHM_VERSION = "matrix-gantry-v7"


def _bbox(value: str) -> tuple[float, float, float, float]:
    try:
        result = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("bbox must be min_lon,min_lat,max_lon,max_lat") from error
    if len(result) != 4 or result[0] >= result[2] or result[1] >= result[3]:
        raise argparse.ArgumentTypeError("bbox minimums must be below maximums")
    return result


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3)


def build_report(
    signs,
    candidates_by_uuid,
    *,
    radius_m: float,
    include_results: bool = False,
    raw_source_count: int | None = None,
    deduped_source_count: int | None = None,
    matches: list[MatrixSignMatch] | None = None,
) -> dict[str, Any]:
    gantries = group_matrix_signs(signs)
    if matches is None:
        matches = match_matrix_results(signs, candidates_by_uuid)
    distances = [
        match.source_distance_m
        for match in matches
        if match.source_distance_m is not None
    ]
    status_counts = Counter(match.status for match in matches)
    confidence_counts = Counter(match.confidence for match in matches if match.confidence)
    failure_counts = Counter(match.failure_reason for match in matches if match.failure_reason)
    method_counts = Counter(match.method for match in matches)
    report: dict[str, Any] = {
        "algorithm_version": ALGORITHM_VERSION,
        "candidate_radius_m": radius_m,
        "source_rows": len(signs),
        "area_raw_source_rows": raw_source_count,
        "area_deduped_source_rows": deduped_source_count,
        "gantries": len(gantries),
        "matched_rows": sum(match.status == "matched" for match in matches),
        "status_counts": dict(sorted(status_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "failure_reason_counts": dict(sorted(failure_counts.items())),
        "method_counts": dict(sorted(method_counts.items())),
        "source_distance_m": {
            "p05": _percentile(distances, 0.05),
            "p50": _percentile(distances, 0.50),
            "p95": _percentile(distances, 0.95),
            "max": round(max(distances), 3) if distances else None,
        },
        "lane_count_mismatch": sum(
            match.failure_reason == "lane_count_mismatch" for match in matches
        ),
    }
    if include_results:
        report["results"] = [match.to_dict() for match in matches]
    return report


def match_matrix_results(signs, candidates_by_uuid) -> list[MatrixSignMatch]:
    """Match a loaded snapshot once, returning results in deterministic order."""

    matches: list[MatrixSignMatch] = []
    for gantry in group_matrix_signs(signs):
        matches.extend(match_matrix_gantry(gantry, candidates_by_uuid))
    return sorted(matches, key=lambda match: match.uuid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", type=_bbox, required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--limit", type=int, default=250, help="bounded physical gantry limit")
    parser.add_argument("--radius-m", type=float, default=20.0)
    parser.add_argument("--candidate-limit", type=int, default=64)
    parser.add_argument("--include-results", action="store_true")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="write this bounded snapshot to road_point_assignment/link",
    )
    parser.add_argument("--output", help="optional JSON output path")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    with SessionLocal() as session:
        snapshot = load_matrix_candidates(
            session,
            bbox=args.bbox,
            source_limit=args.limit,
            radius_m=args.radius_m,
            candidate_limit=args.candidate_limit,
        )
        matches = match_matrix_results(snapshot.signs, snapshot.candidates_by_uuid)
        persisted = False
        if args.persist:
            persist_matrix_matches(
                session,
                snapshot.signs,
                matches,
                algorithm_version=ALGORITHM_VERSION,
            )
            session.commit()
            persisted = True
        report = build_report(
            snapshot.signs,
            snapshot.candidates_by_uuid,
            radius_m=args.radius_m,
            include_results=args.include_results,
            raw_source_count=snapshot.raw_source_count,
            deduped_source_count=snapshot.deduped_source_count,
            matches=matches,
        )
    report["persisted"] = persisted
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    else:
        sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
