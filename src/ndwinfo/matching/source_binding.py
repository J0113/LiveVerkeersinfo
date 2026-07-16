"""Persist NDW measurement-site bindings to the active directed OSM graph.

Matching is deliberately fail-closed. Explicit road/carriageway conflicts and
opposite headings are eliminated before candidates are scored. Ambiguous and
rejected results are persisted for diagnostics but never joined into the
normal road API.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
from typing import Iterable, Literal

from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge
from sqlalchemy import String, and_, cast, delete, func, select, true
from sqlalchemy.orm import Session, aliased

from ndwinfo.config import settings
from ndwinfo.models import (
    MeasurementSite,
    OsmImportRun,
    OsmRoadSegment,
    SourceLocationBinding,
    VildLine,
    VildPoint,
    VildTmc,
)

ALGORITHM_VERSION = "ndw-osm-v4-vild-primary-direction"
SOURCE_TYPE = "ndw_measurement_site"
BindingStatus = Literal["accepted", "ambiguous", "rejected"]


@dataclass(frozen=True)
class SourceTraits:
    road: str | None = None
    carriageway: str | None = None
    heading: float | None = None
    lanes: int | None = None
    carriageway_type: str | None = None
    form_of_way: str | None = None
    direction_conflict: bool = False


@dataclass(frozen=True)
class RoadCandidate:
    internal_segment_id: str
    road_number: str | None
    carriageway_ref: str | None
    lanes: int | None
    distance_m: float
    bearing: float
    highway: str | None = None


@dataclass(frozen=True)
class CandidateScore:
    candidate: RoadCandidate
    score: float
    heading_delta_deg: float | None
    road_match: bool
    carriageway_match: bool
    lane_match: bool


@dataclass(frozen=True)
class BindingDecision:
    status: BindingStatus
    internal_segment_id: str | None
    distance_m: float | None
    heading_delta_deg: float | None
    score: float | None
    margin: float | None
    confidence: float


def decide_binding(
    source: SourceTraits,
    candidates: Iterable[RoadCandidate],
    *,
    max_distance_m: float | None = None,
) -> BindingDecision:
    """Rank bounded candidates after applying hard metadata constraints."""
    if source.direction_conflict:
        return BindingDecision("rejected", None, None, None, None, None, 0.0)
    max_distance = max_distance_m or settings.source_binding_max_distance_m
    source_refs = normalize_road_refs(source.road)
    source_side = normalize_carriageway(source.carriageway)
    location_kind = (source.carriageway_type or "").casefold()
    form_of_way = (source.form_of_way or "").casefold()
    expects_link = "sliproad" in location_kind or "sliproad" in form_of_way
    expects_main = location_kind == "maincarriageway"
    heading = finite_number(source.heading)
    scored: list[CandidateScore] = []

    for candidate in candidates:
        if candidate.distance_m > max_distance:
            continue
        candidate_is_link = (candidate.highway or "").casefold().endswith("_link")
        # This evidence is explicitly supplied by NDW/OpenLR and resolves the
        # common main-road versus ramp ambiguity before distance scoring.
        if expects_link and not candidate_is_link:
            continue
        if expects_main and candidate_is_link:
            continue
        road_refs = normalize_road_refs(candidate.road_number)
        road_match = bool(source_refs and road_refs and source_refs & road_refs)
        if source_refs and road_refs and not road_match:
            continue

        road_side = normalize_carriageway(candidate.carriageway_ref)
        side_match = bool(source_side and road_side and source_side == road_side)
        if source_side and road_side and not side_match:
            continue

        delta = angle_diff(heading, candidate.bearing) if heading is not None else None
        if delta is not None and delta > settings.source_binding_max_heading_delta_deg:
            continue

        lane_match = bool(
            source.lanes is not None
            and candidate.lanes is not None
            and source.lanes == candidate.lanes
        )
        score = candidate.distance_m + (delta * 0.45 if delta is not None else 18.0)
        score -= 22.0 if road_match else 0.0
        score -= 12.0 if side_match else 0.0
        score -= 4.0 if lane_match else 0.0
        scored.append(
            CandidateScore(candidate, score, delta, road_match, side_match, lane_match)
        )

    scored.sort(key=lambda item: (item.score, item.candidate.internal_segment_id))
    if not scored:
        return BindingDecision("rejected", None, None, None, None, None, 0.0)

    best = scored[0]
    second_score = scored[1].score if len(scored) > 1 else best.score + 100.0
    margin = second_score - best.score
    confidence = candidate_confidence(best, margin, max_distance, heading is not None)
    accepted = (
        confidence >= settings.source_binding_min_confidence
        and margin >= settings.source_binding_min_margin
    )
    return BindingDecision(
        "accepted" if accepted else "ambiguous",
        best.candidate.internal_segment_id if accepted else None,
        round(best.candidate.distance_m, 2),
        round(best.heading_delta_deg, 2) if best.heading_delta_deg is not None else None,
        round(best.score, 3),
        round(margin, 3),
        confidence,
    )


def rebuild_measurement_bindings(
    session: Session,
    graph_import_id: int | None = None,
    *,
    source_ids: Iterable[str] | None = None,
    allow_inactive: bool = False,
) -> dict[str, int | str]:
    """Idempotently rebuild measurement-site bindings for one graph version.

    This is an ingest/background operation, never an API request operation.
    Candidate lookup is one streaming ``LATERAL`` query with a bounded,
    indexed ``ST_DWithin`` lookup per source site. This retains index-backed
    nearest-neighbour work without doing one database round trip per site.
    The national road graph is never loaded or scanned in Python.
    """
    graph = _resolve_graph(session, graph_import_id, allow_inactive=allow_inactive)
    ids = list(dict.fromkeys(source_ids or []))
    extent = session.execute(
        select(
            func.ST_XMin(func.ST_Extent(OsmRoadSegment.geom)),
            func.ST_YMin(func.ST_Extent(OsmRoadSegment.geom)),
            func.ST_XMax(func.ST_Extent(OsmRoadSegment.geom)),
            func.ST_YMax(func.ST_Extent(OsmRoadSegment.geom)),
        ).where(OsmRoadSegment.import_run_id == graph.id)
    ).one()
    if any(value is None for value in extent):
        raise RuntimeError(f"OSM graph import {graph.id} contains no road segments")
    # A conservative degree margin is used only for candidate preselection;
    # the authoritative acceptance distance remains metre-based geography.
    margin_deg = settings.source_binding_max_distance_m / 50_000.0
    graph_envelope = func.ST_MakeEnvelope(
        float(extent[0]) - margin_deg,
        float(extent[1]) - margin_deg,
        float(extent[2]) + margin_deg,
        float(extent[3]) + margin_deg,
        4326,
    )
    site_filter = [
        MeasurementSite.geom.isnot(None),
        # A POINT overlapping an envelope is equivalent to intersection, but
        # the bbox operator avoids an unnecessary exact geometry predicate.
        MeasurementSite.geom.op("&&")(graph_envelope),
    ]
    if ids:
        site_filter.append(MeasurementSite.id.in_(ids))

    binding_delete = delete(SourceLocationBinding).where(
        SourceLocationBinding.source_type == SOURCE_TYPE,
        SourceLocationBinding.graph_version == graph.graph_version,
        SourceLocationBinding.algorithm_version == ALGORITHM_VERSION,
    )
    if ids:
        binding_delete = binding_delete.where(SourceLocationBinding.source_id.in_(ids))
    session.execute(binding_delete)

    counts = {"accepted": 0, "ambiguous": 0, "rejected": 0}
    evaluated_at = datetime.now(timezone.utc)
    for site, candidates in _candidate_groups(session, graph.id, site_filter):
        vild_bearing = derive_vild_bearing(
            direction=site["tmc_direction"],
            site_point=site["site_point"],
            line=site["vild_line"],
            primary_point=site["vild_primary_point"],
            positive_point=site["vild_positive_point"],
            negative_point=site["vild_negative_point"],
        )
        heading, direction_source, direction_conflict = resolve_source_direction(
            site["openlr_bearing"], vild_bearing
        )
        decision = decide_binding(
            SourceTraits(
                road=site["road"],
                carriageway=site["carriageway"],
                heading=heading,
                lanes=site["num_lanes"],
                carriageway_type=site["carriageway_type"],
                form_of_way=site["openlr_fow"],
                direction_conflict=direction_conflict,
            ),
            candidates,
        )
        session.add(
            _binding_record(
                graph, site["id"], decision, evaluated_at, direction_source
            )
        )
        counts[decision.status] += 1

    session.flush()
    return {
        "graph_version": graph.graph_version,
        "algorithm_version": ALGORITHM_VERSION,
        **counts,
    }


def _resolve_graph(
    session: Session,
    graph_import_id: int | None,
    *,
    allow_inactive: bool = False,
) -> OsmImportRun:
    query = select(OsmImportRun)
    if graph_import_id is not None:
        query = query.where(OsmImportRun.id == graph_import_id)
    else:
        query = query.where(OsmImportRun.is_active.is_(True))
    graph = session.scalar(query.limit(1))
    if graph is None:
        raise RuntimeError("No requested/active OSM graph import is available")
    shadow_ready = (
        allow_inactive
        and graph_import_id is not None
        and graph.status == "ready"
        and not graph.is_active
    )
    if not shadow_ready and (graph.status != "active" or not graph.is_active):
        raise RuntimeError(f"OSM graph import {graph.id} is not active")
    return graph


def _candidate_groups(
    session: Session,
    graph_import_id: int,
    site_filter: list[object],
) -> Iterable[tuple[dict[str, object], list[RoadCandidate]]]:
    """Stream all source sites and their bounded candidates in one round trip."""
    primary_tmc = aliased(VildTmc, name="primary_tmc")
    positive_tmc = aliased(VildTmc, name="positive_tmc")
    negative_tmc = aliased(VildTmc, name="negative_tmc")
    primary_point = aliased(VildPoint, name="primary_point")
    positive_point = aliased(VildPoint, name="positive_point")
    negative_point = aliased(VildPoint, name="negative_point")
    vild_line = aliased(VildLine, name="bearing_vild_line")
    site_geography = cast(MeasurementSite.geom, Geography)
    distance = func.ST_Distance(
        cast(OsmRoadSegment.geom, Geography), site_geography
    ).label("distance_m")
    candidate = (
        select(
            OsmRoadSegment.internal_segment_id.label("segment_id"),
            OsmRoadSegment.road_number.label("segment_road"),
            OsmRoadSegment.carriageway_ref.label("segment_carriageway"),
            OsmRoadSegment.lanes.label("segment_lanes"),
            OsmRoadSegment.highway.label("segment_highway"),
            distance,
            OsmRoadSegment.geom.label("segment_geom"),
        )
        .where(
            OsmRoadSegment.import_run_id == graph_import_id,
            # The geometry bbox index is materially cheaper for this small
            # local radius. Geography remains the authoritative metre check.
            OsmRoadSegment.geom.op("&&")(
                func.ST_Expand(
                    MeasurementSite.geom,
                    settings.source_binding_max_distance_m / 50_000.0,
                )
            ),
            func.ST_DWithin(
                cast(OsmRoadSegment.geom, Geography),
                site_geography,
                settings.source_binding_max_distance_m,
            ),
        )
        .order_by(distance)
        .limit(settings.source_binding_max_candidates)
        .lateral("candidate")
    )
    query = (
        select(
            MeasurementSite.id.label("site_id"),
            MeasurementSite.road.label("site_road"),
            MeasurementSite.carriageway.label("site_carriageway"),
            MeasurementSite.carriageway_type.label("site_carriageway_type"),
            MeasurementSite.openlr_fow.label("site_openlr_fow"),
            MeasurementSite.openlr_bearing.label("site_bearing"),
            MeasurementSite.tmc_direction.label("site_tmc_direction"),
            MeasurementSite.num_lanes.label("site_lanes"),
            MeasurementSite.geom.label("site_geom"),
            primary_point.geom.label("vild_primary_geom"),
            positive_point.geom.label("vild_positive_geom"),
            negative_point.geom.label("vild_negative_geom"),
            vild_line.geom.label("vild_line_geom"),
            primary_tmc.lin_ref.label("vild_line_ref"),
            positive_tmc.lin_ref.label("vild_positive_line_ref"),
            negative_tmc.lin_ref.label("vild_negative_line_ref"),
            candidate.c.segment_id,
            candidate.c.segment_road,
            candidate.c.segment_carriageway,
            candidate.c.segment_lanes,
            candidate.c.segment_highway,
            candidate.c.distance_m,
            candidate.c.segment_geom,
        )
        .select_from(MeasurementSite)
        .outerjoin(
            primary_tmc,
            and_(
                primary_tmc.loc_nr == MeasurementSite.tmc_primary,
                primary_tmc.country_code == MeasurementSite.tmc_country_code,
                primary_tmc.table_number == MeasurementSite.tmc_table_number,
                primary_tmc.table_version == MeasurementSite.tmc_table_version,
            ),
        )
        .outerjoin(
            positive_tmc,
            and_(
                positive_tmc.loc_nr == primary_tmc.pos_off,
                positive_tmc.country_code == primary_tmc.country_code,
                positive_tmc.table_number == primary_tmc.table_number,
                positive_tmc.table_version == primary_tmc.table_version,
            ),
        )
        .outerjoin(
            negative_tmc,
            and_(
                negative_tmc.loc_nr == primary_tmc.neg_off,
                negative_tmc.country_code == primary_tmc.country_code,
                negative_tmc.table_number == primary_tmc.table_number,
                negative_tmc.table_version == primary_tmc.table_version,
            ),
        )
        .outerjoin(
            primary_point,
            primary_point.id == cast(primary_tmc.loc_nr, String),
        )
        .outerjoin(
            positive_point,
            positive_point.id == cast(positive_tmc.loc_nr, String),
        )
        .outerjoin(
            negative_point,
            negative_point.id == cast(negative_tmc.loc_nr, String),
        )
        .outerjoin(
            vild_line,
            and_(
                vild_line.id == cast(primary_tmc.lin_ref, String),
                func.ST_DWithin(
                    cast(MeasurementSite.geom, Geography),
                    cast(vild_line.geom, Geography),
                    settings.source_binding_vild_max_distance_m,
                ),
            ),
        )
        .outerjoin(candidate, true())
        .where(*site_filter)
        .order_by(MeasurementSite.id, candidate.c.distance_m)
        .execution_options(stream_results=True, yield_per=1000)
    )
    rows = session.execute(query)
    for _, group in groupby(rows, key=lambda row: row.site_id):
        grouped_rows = list(group)
        first = grouped_rows[0]
        point = to_shape(first.site_geom)
        line = _shape_or_none(first.vild_line_geom)
        primary = _shape_or_none(first.vild_primary_geom)
        positive = _same_line_point(
            first.vild_positive_geom,
            first.vild_line_ref,
            first.vild_positive_line_ref,
        )
        negative = _same_line_point(
            first.vild_negative_geom,
            first.vild_line_ref,
            first.vild_negative_line_ref,
        )
        candidates = [
            RoadCandidate(
                internal_segment_id=row.segment_id,
                road_number=row.segment_road,
                carriageway_ref=row.segment_carriageway,
                lanes=row.segment_lanes,
                distance_m=float(row.distance_m),
                bearing=local_line_bearing(to_shape(row.segment_geom), point),
                highway=row.segment_highway,
            )
            for row in grouped_rows
            if row.segment_id is not None
        ]
        yield (
            {
                "id": first.site_id,
                "road": first.site_road,
                "carriageway": first.site_carriageway,
                "carriageway_type": first.site_carriageway_type,
                "openlr_fow": first.site_openlr_fow,
                "openlr_bearing": first.site_bearing,
                "tmc_direction": first.site_tmc_direction,
                "num_lanes": first.site_lanes,
                "site_point": point,
                "vild_line": line,
                "vild_primary_point": primary,
                "vild_positive_point": positive,
                "vild_negative_point": negative,
            },
            candidates,
        )


def _binding_record(
    graph: OsmImportRun,
    source_id: str,
    decision: BindingDecision,
    evaluated_at: datetime,
    direction_source: str | None,
) -> SourceLocationBinding:
    return SourceLocationBinding(
        source_type=SOURCE_TYPE,
        source_id=source_id,
        internal_segment_id=decision.internal_segment_id,
        status=decision.status,
        distance_m=decision.distance_m,
        heading_delta_deg=decision.heading_delta_deg,
        direction_source=direction_source,
        score=decision.score,
        margin=decision.margin,
        confidence=decision.confidence,
        graph_version=graph.graph_version,
        algorithm_version=ALGORITHM_VERSION,
        evaluated_at=evaluated_at,
    )


def candidate_confidence(
    candidate: CandidateScore, margin: float, max_distance_m: float, has_heading: bool
) -> float:
    distance = max(0.0, 1.0 - candidate.candidate.distance_m / max_distance_m)
    heading = (
        max(0.0, 1.0 - candidate.heading_delta_deg / 90.0)
        if candidate.heading_delta_deg is not None
        else 0.35
    )
    confidence = (
        0.4 * distance
        + 0.25 * heading
        + 0.2 * float(candidate.road_match)
        + 0.1 * float(candidate.carriageway_match)
        + 0.05 * float(candidate.lane_match)
    )
    confidence *= min(1.0, 0.55 + max(margin, 0.0) / 18.0)
    if not has_heading:
        confidence = min(confidence, 0.72)
    return round(max(0.0, min(confidence, 1.0)), 3)


def local_line_bearing(line: LineString, point: Point) -> float:
    """Return directed tangent bearing on the segment nearest to ``point``."""
    coords = list(line.coords)
    best: tuple[float, tuple[float, float], tuple[float, float]] | None = None
    for a, b in zip(coords, coords[1:]):
        distance = LineString((a, b)).distance(point)
        if best is None or distance < best[0]:
            best = (distance, a, b)
    if best is None:
        return 0.0
    _, a, b = best
    mean_lat = math.radians((a[1] + b[1]) / 2.0)
    dx = (b[0] - a[0]) * math.cos(mean_lat)
    dy = b[1] - a[1]
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def resolve_source_heading(openlr_bearing: object, vild_bearing: object) -> float | None:
    """Return the primary fixed-sensor heading, or None on a hard conflict."""
    heading, _, conflict = resolve_source_direction(openlr_bearing, vild_bearing)
    return None if conflict else heading


def resolve_source_direction(
    openlr_bearing: object,
    vild_bearing: object,
    *,
    max_agreement_delta_deg: float = 45.0,
) -> tuple[float | None, str | None, bool]:
    """Use nationally complete VILD direction with OpenLR as cross-check.

    VILD supplies a relative direction for every in-scope fixed speed site and
    its chain topology orients the local line tangent. OpenLR remains a useful
    independent fallback/check. Material disagreement fails closed instead of
    choosing whichever source happens to be nearer to an OSM candidate.
    """
    openlr = finite_number(openlr_bearing)
    vild = finite_number(vild_bearing)
    openlr = openlr % 360.0 if openlr is not None else None
    vild = vild % 360.0 if vild is not None else None
    if openlr is not None and vild is not None:
        if angle_diff(openlr, vild) > max_agreement_delta_deg:
            return None, "conflict", True
        return vild, "vild", False
    if vild is not None:
        return vild, "vild", False
    if openlr is not None:
        return openlr, "openlr", False
    return None, None, False


def derive_vild_bearing(
    *,
    direction: object,
    site_point: object,
    line: object,
    primary_point: object,
    positive_point: object = None,
    negative_point: object = None,
) -> float | None:
    """Derive a local directed bearing from an unambiguous VILD TMC reference.

    VILD line coordinate order is not treated as travel direction. The selected
    TMC neighbour establishes the direction along the line; the bearing itself
    is the local tangent nearest to the measurement site. Corrupt topology,
    line transitions and unknown direction spellings therefore fail closed.
    """
    normalized_direction = normalize_tmc_direction(direction)
    line_string = _nearest_line_string(line, site_point)
    if (
        normalized_direction is None
        or line_string is None
        or not isinstance(site_point, Point)
        or not isinstance(primary_point, Point)
    ):
        return None

    primary_offset = line_string.project(primary_point)
    positive_offset = _project_distinct(line_string, positive_point, primary_offset)
    negative_offset = _project_distinct(line_string, negative_point, primary_offset)

    # If both topology arms exist, they must lie on opposite sides. This avoids
    # assigning a direction from internally inconsistent or duplicated points.
    if positive_offset is not None and negative_offset is not None:
        positive_sign = math.copysign(1.0, positive_offset - primary_offset)
        negative_sign = math.copysign(1.0, negative_offset - primary_offset)
        if positive_sign == negative_sign:
            return None

    target_offset = (
        positive_offset if normalized_direction == "positive" else negative_offset
    )
    if target_offset is None:
        return None

    bearing = local_line_bearing(line_string, site_point)
    if target_offset < primary_offset:
        bearing = (bearing + 180.0) % 360.0
    return bearing


def normalize_tmc_direction(value: object) -> Literal["positive", "negative"] | None:
    """Normalize known textual variants without guessing numeric provider codes."""
    if value is None:
        return None
    raw = str(value).strip().casefold()
    if raw == "+":
        return "positive"
    if raw == "-":
        return "negative"
    normalized = re.sub(r"[\s_-]+", "", raw)
    if normalized in {"positive", "positief", "pos", "+"}:
        return "positive"
    if normalized in {"negative", "negatief", "neg", "-"}:
        return "negative"
    return None


def _nearest_line_string(line: object, point: object) -> LineString | None:
    if not isinstance(point, Point):
        return None
    if isinstance(line, LineString):
        return line if len(line.coords) >= 2 and not line.is_empty else None
    if isinstance(line, MultiLineString):
        merged = linemerge(line)
        if isinstance(merged, LineString):
            return merged if len(merged.coords) >= 2 and not merged.is_empty else None
        parts = [part for part in merged.geoms if len(part.coords) >= 2]
        return min(parts, key=lambda part: part.distance(point)) if parts else None
    return None


def _project_distinct(
    line: LineString, point: object, primary_offset: float
) -> float | None:
    if not isinstance(point, Point):
        return None
    offset = line.project(point)
    # Coordinates are WGS84 degrees here. One nanodegree is far below source
    # precision, while still rejecting duplicate/same-position topology points.
    if not math.isfinite(offset) or abs(offset - primary_offset) <= 1e-9:
        return None
    return offset


def _shape_or_none(value: object) -> object | None:
    return to_shape(value) if value is not None else None


def _same_line_point(value: object, line_ref: object, neighbour_ref: object) -> object | None:
    if value is None or line_ref is None or neighbour_ref != line_ref:
        return None
    return to_shape(value)


def normalize_road_refs(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        f"{prefix}{int(number)}{suffix}"
        for prefix, number, suffix in re.findall(
            r"\b([A-Z]{1,3})\s*0*(\d+)([A-Z]?)\b", str(value).upper()
        )
    }


def normalize_carriageway(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"R", "RIGHT", "RECHTS", "RE"}:
        return "R"
    if normalized in {"L", "LEFT", "LINKS", "LI"}:
        return "L"
    # NDW also uses DVK letters for main/parallel or otherwise distinct
    # carriageways. OSM may carry the same value in carriageway_ref. Preserve a
    # simple token for exact, case-insensitive comparison; never infer its side.
    if re.fullmatch(r"[A-Z]{1,3}", normalized):
        return normalized
    return None


def finite_number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)
