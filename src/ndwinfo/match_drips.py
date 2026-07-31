"""Run a bounded DRIP/VMS-to-OSM point match.

The matcher keeps the original DRIP point and writes only explainable point
assignments/links.  A 60 m primary search is extended to 500 m only when the
panel bearing leaves one directed traversal.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from typing import Any

from ndwinfo.db import SessionLocal
from ndwinfo.road_matching.candidates import load_drip_candidates
from ndwinfo.road_matching.drips import (
    DRIP_EXTENDED_RADIUS_M,
    DRIP_PRIMARY_RADIUS_M,
    match_drip_results,
)
from ndwinfo.road_matching.persistence import persist_drip_matches

# v2: bearing decides inside the 2 m distance tie band, the 60-500 m tail also
# requires the bearing to agree within 20 degrees, and a contradicted panel
# route is recorded as a conflict instead of neutral corridor evidence.
ALGORITHM_VERSION = "drip-point-v2"


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
    drips,
    matches,
    *,
    primary_radius_m: float,
    extended_radius_m: float,
    raw_source_count: int | None = None,
    include_results: bool = False,
) -> dict[str, Any]:
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
        "candidate_radius_m": extended_radius_m,
        "primary_radius_m": primary_radius_m,
        "source_rows": len(drips),
        "area_raw_source_rows": raw_source_count,
        "truncated": raw_source_count is not None and raw_source_count > len(drips),
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
        "extended_matches": sum(
            match.status == "matched"
            and match.source_distance_m is not None
            and match.source_distance_m > primary_radius_m
            for match in matches
        ),
        "working_status_counts": dict(
            sorted(
                (
                    (status or "unknown", count)
                    for status, count in Counter(drip.working_status for drip in drips).items()
                ),
                key=lambda item: item[0],
            )
        ),
    }
    if include_results:
        report["results"] = [match.to_dict() for match in matches]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", type=_bbox, required=True, help="min_lon,min_lat,max_lon,max_lat")
    parser.add_argument("--limit", type=int, default=1000, help="bounded DRIP source-row limit")
    parser.add_argument("--primary-radius-m", type=float, default=DRIP_PRIMARY_RADIUS_M)
    parser.add_argument("--extended-radius-m", type=float, default=DRIP_EXTENDED_RADIUS_M)
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
    if args.candidate_limit < 1:
        parser.error("--candidate-limit must be positive")
    if args.primary_radius_m <= 0 or args.extended_radius_m < args.primary_radius_m:
        parser.error("extended radius must be >= a positive primary radius")
    with SessionLocal() as session:
        snapshot = load_drip_candidates(
            session,
            bbox=args.bbox,
            source_limit=args.limit,
            radius_m=args.extended_radius_m,
            candidate_limit=args.candidate_limit,
        )
        matches = match_drip_results(
            snapshot.drips,
            snapshot.candidates_by_key,
            primary_radius_m=args.primary_radius_m,
            extended_radius_m=args.extended_radius_m,
        )
        persisted = False
        if args.persist:
            persist_drip_matches(
                session,
                snapshot.drips,
                matches,
                algorithm_version=ALGORITHM_VERSION,
            )
            session.commit()
            persisted = True
        report = build_report(
            snapshot.drips,
            matches,
            primary_radius_m=args.primary_radius_m,
            extended_radius_m=args.extended_radius_m,
            raw_source_count=snapshot.raw_source_count,
            include_results=args.include_results,
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
