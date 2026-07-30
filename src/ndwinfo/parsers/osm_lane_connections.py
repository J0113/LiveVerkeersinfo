"""Directed lane-to-lane connections for the independent OSM Lanes layer."""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pyproj import Transformer
from shapely import from_wkt
from shapely.geometry import LineString, Point
from shapely.ops import substring, transform
from shapely.strtree import STRtree

from ndwinfo.geometry.directed_lines import (
    AMBIGUOUS_ANGLE_DELTA_DEG,
    JUNCTION_BOX_RADIUS_M,
    MAX_TURN_ANGLE_DEG,
    angle_delta_deg,
    bearing_deg,
    bounded_cubic_bezier,
    turn_token_matches,
    unit_vector,
)

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)
ENDPOINT_TOUCH_TOLERANCE_M = 0.05
SOURCE_ENDPOINT_EXACT_TOLERANCE_M = 0.5
DOMINANCE_DISTANCE_TOLERANCE_M = 0.5
DOMINANCE_MAX_INTERMEDIATE_SEGMENTS = 2
COUNT_TRANSITION_MAX_ENDPOINT_GAP_M = 8.0
COUNT_TRANSITION_MAX_ANGLE_DEG = 30.0
LINK_TRANSITION_MAX_TRIM_ANGLE_DEG = 45.0
LINK_NEAR_STRAIGHT_MAX_ANGLE_DEG = 15.0
COUNT_TRANSITION_MIN_TRIM_M = 5.0
COUNT_TRANSITION_MAX_TRIM_M = 15.0
LINK_TRANSITION_MAX_TRIM_M = 25.0
LINK_NEAR_STRAIGHT_MAX_TRIM_M = 50.0
COUNT_TRANSITION_TRIM_PER_GAP = 4.0
LINK_NEAR_STRAIGHT_TRIM_PER_GAP = 8.0
COUNT_TRANSITION_MAX_LINE_FRACTION = 0.4
EQUAL_COUNT_TRANSITION_MIN_ENDPOINT_GAP_M = 0.75
LINK_TRANSITION_MAX_LINE_FRACTION = 0.8
MINIMUM_VISIBLE_LENGTH_M = 2.0
MAXIMUM_TOTAL_TRIM_FRACTION = 0.8
TRANSITION_PLACEMENT_MAX_ANGLE_DEG = 60.0
TRANSITION_PLACEMENT_MAX_ENDPOINT_SHIFT_M = 25.0
TRANSITION_PLACEMENT_SPACING_TOLERANCE_M = 0.25
TRANSITION_PLACEMENT_MAX_SAMPLES = 64
MULTI_SOURCE_BLOCK_MAX_ANGLE_DEG = 30.0
MULTI_SOURCE_LINK_BLOCK_MAX_ANGLE_DEG = 45.0
MULTI_SOURCE_BLOCK_LOOKBACK_M = 30.0
MULTI_SOURCE_BLOCK_MIN_LATERAL_DELTA_M = 1.0

TURNING_TOKENS = {
    "left",
    "slight_left",
    "sharp_left",
    "right",
    "slight_right",
    "sharp_right",
    "reverse",
}
LEFT_TURNING_TOKENS = {"left", "slight_left", "sharp_left"}
RIGHT_TURNING_TOKENS = {"right", "slight_right", "sharp_right"}
PRIMARY_TOKENS = {"none", "through", "merge_to_left", "merge_to_right"}
MERGE_TOKENS = {"merge_to_left", "merge_to_right"}
PLACEMENT_PREFIXES = {"right_of", "middle_of", "left_of"}
CHANGE_LATERAL_ALLOWED = {
    "yes": frozenset({"left", "right"}),
    "no": frozenset(),
    "not_left": frozenset({"right"}),
    "not_right": frozenset({"left"}),
    "only_left": frozenset({"left"}),
    "only_right": frozenset({"right"}),
}


@dataclass(frozen=True)
class RoadContext:
    road_id: int
    highway: str | None
    tags: dict[str, Any]

    @property
    def is_link(self) -> bool:
        return bool(self.highway and self.highway.endswith("_link"))

    @property
    def is_roundabout(self) -> bool:
        return str(self.tags.get("junction", "")).lower() == "roundabout"

    @property
    def is_oneway(self) -> bool:
        value = str(self.tags.get("oneway", "")).strip().lower()
        return value in {"yes", "true", "1", "-1"} or (
            self.is_roundabout and value != "no"
        )


@dataclass(frozen=True)
class LaneTraversal:
    lane_id: str
    direction: str
    stored_direction: str
    road_id: int
    segment_id: str
    lane_nr: int
    lane_count: int
    line_wgs84: LineString
    source_line_wgs84: LineString | None
    entry_node_id: int
    exit_node_id: int
    context: RoadContext

    @property
    def id(self) -> str:
        return f"{self.lane_id}@{self.direction}"

    @property
    def entry(self) -> tuple[float, float]:
        return tuple(self.line_wgs84.coords[0])

    @property
    def exit(self) -> tuple[float, float]:
        return tuple(self.line_wgs84.coords[-1])


@dataclass(frozen=True)
class TraversalGroup:
    segment_id: str
    direction: str
    lanes: tuple[LaneTraversal, ...]

    @property
    def representative(self) -> LaneTraversal:
        return self.lanes[0]


@dataclass(frozen=True)
class MovementCandidate:
    source: TraversalGroup
    target: TraversalGroup
    angle_deg: float
    distance_m: float
    adjacency_evidence: str
    kind: str
    score: tuple[float, ...]
    suppressed_reason: str | None = None
    dominated_via: tuple[str, ...] = ()

    @property
    def exact(self) -> bool:
        return self.adjacency_evidence in {"node_exact", "endpoint_exact", "manual"}


@dataclass(frozen=True)
class ParsedLaneFields:
    key: str | None
    raw: str | None
    fields: tuple[frozenset[str], ...]
    expected_count: int
    actual_count: int
    valid: bool

    @property
    def tokens(self) -> frozenset[str]:
        return frozenset(token for field in self.fields for token in field)


@dataclass(frozen=True)
class PlacementAnchor:
    key: str
    raw: str
    kind: str
    lane_nr: int | None


@dataclass(frozen=True)
class TopologyEdge:
    source: LaneTraversal
    target: LaneTraversal
    movement: MovementCandidate
    connection_type: str
    minimum_trim_m: float = 0.0
    allocation_evidence: str | None = None


def _as_line(value: Any) -> LineString:
    if isinstance(value, LineString):
        return value
    return from_wkt(str(value))


def _context_for(
    road_id: int, contexts: Mapping[int, RoadContext | Mapping[str, Any]] | None
) -> RoadContext:
    context = (contexts or {}).get(road_id)
    if isinstance(context, RoadContext):
        return context
    data = dict(context or {})
    tags = dict(data.get("tags") or data.get("raw") or {})
    highway = data.get("highway") or tags.get("highway")
    return RoadContext(road_id, highway, tags)


def lane_traversals(
    rows: Iterable[Mapping[str, Any]],
    road_contexts: Mapping[int, RoadContext | Mapping[str, Any]] | None = None,
) -> list[LaneTraversal]:
    """Expand physical centerlines into directed in-memory traversals."""
    traversals: list[LaneTraversal] = []
    for row in rows:
        road_id = int(row["road_id"])
        direction = str(row["direction"])
        line = _as_line(row["geom"])
        raw = dict(row.get("raw") or {})
        start_node = int(raw.get("start_node_id") or 0)
        end_node = int(raw.get("end_node_id") or 0)
        source_start = raw.get("source_start")
        source_end = raw.get("source_end")
        source_line = None
        if (
            isinstance(source_start, (list, tuple))
            and len(source_start) >= 2
            and isinstance(source_end, (list, tuple))
            and len(source_end) >= 2
        ):
            source_line = LineString(
                [
                    (float(source_start[0]), float(source_start[1])),
                    (float(source_end[0]), float(source_end[1])),
                ]
            )
        directions = ("fwd", "bwd") if direction == "both" else (direction,)
        if direction == "unknown":
            continue
        for travel_direction in directions:
            # fwd/bwd rows are already persisted in travel order. Shared rows
            # alone are source-ordered and need an in-memory reverse for @bwd.
            traversal_line = line
            if direction == "both" and travel_direction == "bwd":
                traversal_line = LineString(list(line.coords)[::-1])
            traversal_source_line = source_line
            if source_line is not None and travel_direction == "bwd":
                traversal_source_line = LineString(list(source_line.coords)[::-1])
            entry_node, exit_node = (
                (start_node, end_node)
                if travel_direction == "fwd"
                else (end_node, start_node)
            )
            traversals.append(
                LaneTraversal(
                    lane_id=str(row["id"]),
                    direction=travel_direction,
                    stored_direction=direction,
                    road_id=road_id,
                    segment_id=str(row["segment_id"]),
                    lane_nr=int(row["lane_nr"]),
                    lane_count=int(row["lane_count"]),
                    line_wgs84=traversal_line,
                    source_line_wgs84=traversal_source_line,
                    entry_node_id=entry_node,
                    exit_node_id=exit_node,
                    context=_context_for(road_id, road_contexts),
                )
            )
    return traversals


def _groups(traversals: Iterable[LaneTraversal]) -> list[TraversalGroup]:
    grouped: dict[tuple[str, str], list[LaneTraversal]] = {}
    for traversal in traversals:
        grouped.setdefault((traversal.segment_id, traversal.direction), []).append(traversal)
    return [
        TraversalGroup(segment_id, direction, tuple(sorted(lanes, key=lambda lane: lane.lane_nr)))
        for (segment_id, direction), lanes in grouped.items()
    ]


def _group_geometry(group: TraversalGroup) -> LineString:
    return transform(_WGS84_TO_RD.transform, group.representative.line_wgs84)


def _group_source_geometry(group: TraversalGroup) -> LineString | None:
    line = group.representative.source_line_wgs84
    return transform(_WGS84_TO_RD.transform, line) if line is not None else None


def _adjacency_evidence(
    source: TraversalGroup,
    target: TraversalGroup,
    endpoint_distance_m: float,
) -> str:
    source_node = source.representative.exit_node_id
    target_node = target.representative.entry_node_id
    if source_node and source_node == target_node:
        return "node_exact"
    # Conflicting non-zero node IDs are meaningful OSM topology. Coordinate
    # coincidence must not silently override them.
    if source_node and target_node:
        return "junction_box"
    if (
        source.representative.source_line_wgs84 is not None
        and target.representative.source_line_wgs84 is not None
        and endpoint_distance_m <= SOURCE_ENDPOINT_EXACT_TOLERANCE_M
    ):
        return "endpoint_exact"
    return "junction_box"


def _is_reverse_oneway(tags: Mapping[str, Any]) -> bool:
    return str(tags.get("oneway", "")).strip().lower() == "-1"


def _directional_lane_tag_keys(
    base: str,
    direction: str,
    tags: Mapping[str, Any],
) -> tuple[str, ...]:
    if direction == "fwd":
        return f"{base}:forward", base
    if _is_reverse_oneway(tags):
        return f"{base}:backward", base
    return (f"{base}:backward",)


def _parse_lane_fields(group: TraversalGroup, base: str) -> ParsedLaneFields:
    lane_count = group.representative.lane_count
    tags = group.representative.context.tags
    for key in _directional_lane_tag_keys(base, group.direction, tags):
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        raw_fields = value.split("|")
        fields = tuple(
            frozenset(
                token.strip().lower()
                for token in lane_field.split(";")
                if token.strip()
            )
            for lane_field in raw_fields
        )
        return ParsedLaneFields(
            key=key,
            raw=value,
            fields=fields,
            expected_count=lane_count,
            actual_count=len(raw_fields),
            valid=len(raw_fields) == lane_count,
        )
    return ParsedLaneFields(
        key=None,
        raw=None,
        fields=(),
        expected_count=lane_count,
        actual_count=0,
        valid=False,
    )


def _turn_fields(group: TraversalGroup) -> ParsedLaneFields:
    return _parse_lane_fields(group, "turn:lanes")


def _turn_tokens(group: TraversalGroup) -> set[str]:
    parsed = _turn_fields(group)
    return set(parsed.tokens) if parsed.valid else set()


def _lane_turn_tokens(lane: LaneTraversal) -> set[str]:
    """Return cardinality-valid turn tokens attached to one travel lane."""
    group = TraversalGroup(lane.segment_id, lane.direction, (lane,))
    parsed = _parse_lane_fields(group, "turn:lanes")
    if not parsed.valid or not 1 <= lane.lane_nr <= len(parsed.fields):
        return set()
    return set(parsed.fields[lane.lane_nr - 1])


def _lane_field_tokens(lane: LaneTraversal, base: str) -> set[str]:
    group = TraversalGroup(lane.segment_id, lane.direction, (lane,))
    parsed = _parse_lane_fields(group, base)
    if not parsed.valid or not 1 <= lane.lane_nr <= len(parsed.fields):
        return set()
    return set(parsed.fields[lane.lane_nr - 1])


def _parse_placement_value(key: str, value: Any) -> PlacementAnchor | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized == "transition":
        return PlacementAnchor(key, value, "transition", None)
    kind, separator, lane_text = normalized.partition(":")
    if not separator or kind not in PLACEMENT_PREFIXES:
        return None
    try:
        lane_nr = int(lane_text)
    except ValueError:
        return None
    if lane_nr <= 0:
        return None
    return PlacementAnchor(key, value, kind, lane_nr)


def _placement_anchor(
    group: TraversalGroup,
    *,
    outgoing: bool,
) -> PlacementAnchor | None:
    tags = group.representative.context.tags
    coordinate_end = (
        "end"
        if (group.direction == "fwd") == outgoing
        else "start"
    )
    for key in (
        f"placement:{coordinate_end}",
        f"placement:{'forward' if group.direction == 'fwd' else 'backward'}",
        "placement",
    ):
        if key not in tags:
            continue
        return _parse_placement_value(key, tags.get(key))
    return None


def _lane_change_allowed(lane: LaneTraversal, lateral: str) -> bool | None:
    tokens = _lane_field_tokens(lane, "change:lanes")
    if not tokens:
        return None
    allowed_sets = [
        CHANGE_LATERAL_ALLOWED[token]
        for token in tokens
        if token in CHANGE_LATERAL_ALLOWED
    ]
    if not allowed_sets:
        return None
    allowed = set.union(*(set(item) for item in allowed_sets))
    return lateral in allowed


def _tagged_merge_assignments(
    source: Sequence[LaneTraversal],
    target: Sequence[LaneTraversal],
) -> list[tuple[LaneTraversal, LaneTraversal]] | None:
    """Map a shrinking cross-section using per-lane merge_to_* tags.

    Non-merging lanes retain their left-to-right order. Each disappearing lane
    joins the nearest surviving lane on the side named by its OSM token. This
    deliberately permits many source lanes to connect to one target lane.
    """
    if len(source) <= len(target):
        return None
    merge_directions: dict[int, int] = {}
    for index, lane in enumerate(source):
        tokens = _lane_turn_tokens(lane)
        if "merge_to_left" in tokens:
            merge_directions[index] = -1
        elif "merge_to_right" in tokens:
            merge_directions[index] = 1
    if len(merge_directions) != len(source) - len(target):
        return None

    survivor_indexes = [
        index for index in range(len(source)) if index not in merge_directions
    ]
    if len(survivor_indexes) != len(target):
        return None
    target_by_source = dict(zip(survivor_indexes, target))
    assignments = [
        (source[source_index], target_lane)
        for source_index, target_lane in target_by_source.items()
    ]
    for source_index, step in merge_directions.items():
        neighbor = source_index + step
        while 0 <= neighbor < len(source) and neighbor in merge_directions:
            neighbor += step
        if neighbor not in target_by_source:
            return None
        assignments.append((source[source_index], target_by_source[neighbor]))
    return sorted(assignments, key=lambda pair: pair[0].lane_nr)


def _candidate_kind(source: TraversalGroup, target: TraversalGroup, angle: float) -> str | None:
    source_context = source.representative.context
    target_context = target.representative.context
    if not source_context.is_link and target_context.is_link and abs(angle) <= 45.0:
        return "exit"
    if source_context.is_link and not target_context.is_link:
        return "entry"
    if source_context.is_roundabout or target_context.is_roundabout:
        return "roundabout"
    if any(
        token in TURNING_TOKENS and turn_token_matches(token, angle)
        for token in _turn_tokens(source)
    ):
        # Explicit directional lane evidence identifies a branch even when
        # source and target share the same route ref. Otherwise a same-ref
        # slip road is treated as the primary continuation and a many-to-one
        # fallback can select the wrong edge of the source cross-section.
        return "tagged"
    if (
        source_context.tags.get("ref")
        and source_context.tags.get("ref") == target_context.tags.get("ref")
    ):
        return "continuation"
    if any(turn_token_matches(token, angle) for token in _turn_tokens(source)):
        return "tagged"
    return "continuation"


def _layer_value(context: RoadContext) -> float:
    """Return the OSM layer, treating the untagged road level as zero."""
    try:
        return float(str(context.tags.get("layer", "0")).strip())
    except ValueError:
        return 0.0


def _grade_structure(context: RoadContext) -> str:
    tags = context.tags
    if str(tags.get("bridge", "")).strip().lower() not in {"", "no", "false", "0"}:
        return "bridge"
    if str(tags.get("tunnel", "")).strip().lower() not in {"", "no", "false", "0"}:
        return "tunnel"
    if str(tags.get("covered", "")).strip().lower() not in {"", "no", "false", "0"}:
        return "covered"
    return "surface"


def _junction_box_grade_compatible(
    source_context: RoadContext,
    target_context: RoadContext,
) -> bool:
    """Reject proximity-only movements between visibly different road levels."""
    if not math.isclose(
        _layer_value(source_context),
        _layer_value(target_context),
        abs_tol=0.01,
    ):
        return False
    source_structure = _grade_structure(source_context)
    target_structure = _grade_structure(target_context)
    return source_structure == target_structure


def _eligible(
    source: TraversalGroup,
    target: TraversalGroup,
    *,
    exact: bool,
    angle: float,
) -> bool:
    source_lane = source.representative
    target_lane = target.representative
    if source.segment_id == target.segment_id:
        return False
    if source_lane.lane_id == target_lane.lane_id:
        return False
    if abs(angle) > MAX_TURN_ANGLE_DEG:
        return False
    source_context, target_context = source_lane.context, target_lane.context
    if not exact and not _junction_box_grade_compatible(
        source_context,
        target_context,
    ):
        return False
    link_transition = source_context.is_link != target_context.is_link
    if not exact and link_transition and abs(angle) > 45.0:
        return False
    if (
        not exact
        and not source_context.is_link
        and not target_context.is_link
        and (
            source_context.highway == "motorway"
            or target_context.highway == "motorway"
        )
        and source_context.highway != target_context.highway
    ):
        # Motorway access is represented through a motorway_link. A nearby
        # ordinary road with a compatible turn token is not direct topology.
        return False
    same_ref = bool(
        source_context.tags.get("ref")
        and source_context.tags.get("ref") == target_context.tags.get("ref")
    )
    same_name = bool(
        source_context.tags.get("name")
        and source_context.tags.get("name") == target_context.tags.get("name")
    )
    roundabout = source_context.is_roundabout or target_context.is_roundabout
    tagged = any(turn_token_matches(token, angle) for token in _turn_tokens(source))
    return exact or same_ref or same_name or link_transition or roundabout or tagged


def discover_movement_candidates(
    groups: Sequence[TraversalGroup],
) -> dict[tuple[str, str], list[MovementCandidate]]:
    """Discover node/endpoint-exact and filtered 25 m road movements."""
    groups = [
        group for group in groups if _group_source_geometry(group) is not None
    ]
    geometries = {id(group): _group_geometry(group) for group in groups}
    source_geometries = {
        id(group): _group_source_geometry(group)
        for group in groups
    }
    entry_points = [
        Point(source_geometries[id(group)].coords[0])
        for group in groups
    ]
    entry_tree = STRtree(entry_points)
    by_entry_node: dict[int, list[int]] = defaultdict(list)
    for index, group in enumerate(groups):
        node_id = group.representative.entry_node_id
        if node_id:
            by_entry_node[node_id].append(index)
    result: dict[tuple[str, str], list[MovementCandidate]] = {}
    for source in groups:
        source_line = geometries[id(source)]
        source_original = source_geometries[id(source)]
        source_exit = Point(source_original.coords[-1])
        source_bearing_line = source_line
        source_anchor = _placement_anchor(source, outgoing=True)
        if (
            source_original is not None
            and source_anchor is not None
            and source_anchor.kind == "transition"
        ):
            # A resolved placement transition inherits its endpoint tangent
            # from a preliminary lane allocation. Reusing that derived tangent
            # to allocate the same junction creates a feedback loop, so retain
            # the original OSM-way direction for movement classification.
            source_bearing_line = source_original
        source_bearing = bearing_deg(
            source_bearing_line.coords[-2],
            source_bearing_line.coords[-1],
        )
        candidates = []
        nearby_indexes = {
            int(index)
            for index in entry_tree.query(
                source_exit.buffer(JUNCTION_BOX_RADIUS_M)
            )
        }
        nearby_indexes.update(
            by_entry_node.get(source.representative.exit_node_id, ())
        )
        for target_index in sorted(nearby_indexes):
            target = groups[target_index]
            if target is source:
                continue
            target_line = geometries[id(target)]
            target_original = source_geometries[id(target)]
            target_entry = Point(target_original.coords[0])
            distance = source_exit.distance(target_entry)
            evidence = _adjacency_evidence(source, target, distance)
            if evidence == "junction_box" and distance > JUNCTION_BOX_RADIUS_M:
                continue
            target_bearing_line = target_line
            target_anchor = _placement_anchor(target, outgoing=False)
            if (
                target_original is not None
                and target_anchor is not None
                and target_anchor.kind == "transition"
            ):
                target_bearing_line = target_original
            target_bearing = bearing_deg(
                target_bearing_line.coords[0],
                target_bearing_line.coords[1],
            )
            angle = angle_delta_deg(source_bearing, target_bearing)
            exact = evidence != "junction_box"
            if not _eligible(source, target, exact=exact, angle=angle):
                continue
            kind = _candidate_kind(source, target, angle)
            if kind is None:
                continue
            source_context, target_context = (
                source.representative.context,
                target.representative.context,
            )
            same_ref = bool(
                source_context.tags.get("ref")
                and source_context.tags.get("ref") == target_context.tags.get("ref")
            )
            same_name = bool(
                source_context.tags.get("name")
                and source_context.tags.get("name") == target_context.tags.get("name")
            )
            score = (
                {
                    "node_exact": 0.0,
                    "endpoint_exact": 1.0,
                    "junction_box": 2.0,
                }[evidence],
                0.0 if same_ref else 1.0,
                0.0 if same_name else 1.0,
                abs(angle),
                distance,
            )
            candidates.append(
                MovementCandidate(
                    source,
                    target,
                    angle,
                    distance,
                    evidence,
                    kind,
                    score,
                )
            )
        result[(source.segment_id, source.direction)] = candidates
    return result


def suppress_dominated_candidates(
    discovered: Mapping[tuple[str, str], Sequence[MovementCandidate]],
) -> tuple[
    dict[tuple[str, str], list[MovementCandidate]],
    list[dict[str, Any]],
]:
    """Remove box candidates reachable through one or two short successors."""
    immediate: dict[tuple[str, str], list[MovementCandidate]] = {
        key: [candidate for candidate in candidates if candidate.exact]
        for key, candidates in discovered.items()
    }
    exact_predecessors: dict[
        tuple[str, str], list[MovementCandidate]
    ] = defaultdict(list)
    for candidates in immediate.values():
        for candidate in candidates:
            exact_predecessors[
                (candidate.target.segment_id, candidate.target.direction)
            ].append(candidate)
    kept: dict[tuple[str, str], list[MovementCandidate]] = {}
    diagnostics: list[dict[str, Any]] = []
    for key, candidates in discovered.items():
        source_successors = immediate.get(key, [])
        for candidate in candidates:
            if candidate.exact:
                kept.setdefault(key, []).append(candidate)
                continue
            target_key = (
                candidate.target.segment_id,
                candidate.target.direction,
            )
            if (
                not candidate.source.representative.context.is_link
                and candidate.target.representative.context.is_link
                and exact_predecessors.get(target_key)
            ):
                diagnostics.append(
                    {
                        "reason": "existing_link_predecessor_rejects_new_exit",
                        "from": candidate.source.representative.id,
                        "to": candidate.target.representative.id,
                        "exact_predecessors": sorted(
                            predecessor.source.segment_id
                            for predecessor in exact_predecessors[target_key]
                        ),
                    }
                )
                continue
            if not source_successors:
                kept.setdefault(key, []).append(candidate)
                diagnostics.append(
                    {
                        "reason": "dominance_not_proven_no_successor",
                        "from": candidate.source.representative.id,
                        "to": candidate.target.representative.id,
                    }
                )
                continue

            queue: deque[tuple[TraversalGroup, tuple[str, ...], float, int]] = deque()
            for successor in source_successors:
                successor_length = _group_geometry(successor.target).length
                queue.append(
                    (
                        successor.target,
                        (successor.target.segment_id,),
                        successor_length,
                        0,
                    )
                )
            dominated_via: tuple[str, ...] | None = None
            seen: set[tuple[str, str, int]] = set()
            while queue:
                current, path, distance_m, intermediate_count = queue.popleft()
                current_key = (current.segment_id, current.direction)
                if (
                    current_key == target_key
                    and 1 <= intermediate_count <= DOMINANCE_MAX_INTERMEDIATE_SEGMENTS
                ):
                    dominated_via = path[:-1]
                    break
                if (
                    distance_m
                    > JUNCTION_BOX_RADIUS_M + DOMINANCE_DISTANCE_TOLERANCE_M
                    or intermediate_count > DOMINANCE_MAX_INTERMEDIATE_SEGMENTS
                ):
                    continue
                state = (*current_key, intermediate_count)
                if state in seen:
                    continue
                seen.add(state)
                for edge in immediate.get(current_key, ()):
                    next_group = edge.target
                    queue.append(
                        (
                            next_group,
                            (*path, next_group.segment_id),
                            distance_m + _group_geometry(next_group).length,
                            intermediate_count + 1,
                        )
                    )
            if dominated_via is None:
                kept.setdefault(key, []).append(candidate)
                continue
            suppressed = replace(
                candidate,
                suppressed_reason="intermediate_segment_dominates",
                dominated_via=dominated_via,
            )
            diagnostics.append(
                {
                    "reason": suppressed.suppressed_reason,
                    "from": suppressed.source.representative.id,
                    "to": suppressed.target.representative.id,
                    "dominated_via": list(suppressed.dominated_via),
                    "distance_m": round(suppressed.distance_m, 2),
                }
            )
    for key in discovered:
        kept.setdefault(key, [])
    return kept, diagnostics


def choose_movement_set(
    candidates: Sequence[MovementCandidate],
) -> tuple[list[MovementCandidate], dict[str, Any] | None]:
    """Choose one primary continuation plus independently eligible branches."""
    branches = [
        candidate for candidate in candidates if candidate.kind in {"exit", "roundabout", "tagged"}
    ]
    primary_candidates = [
        candidate
        for candidate in candidates
        if candidate.kind in {"continuation", "entry"}
    ]
    primary_candidates.sort(key=lambda candidate: candidate.score)
    diagnostic = None
    selected: list[MovementCandidate] = []
    if primary_candidates:
        best = primary_candidates[0]
        if (
            len(primary_candidates) > 1
            and primary_candidates[1].score[:3] == best.score[:3]
            and abs(primary_candidates[1].angle_deg - best.angle_deg)
            < AMBIGUOUS_ANGLE_DELTA_DEG
        ):
            diagnostic = {
                "reason": "ambiguous outgoing movement",
                "candidates": [
                    {
                        "segment_id": candidate.target.segment_id,
                        "direction": candidate.target.direction,
                        "angle_deg": round(candidate.angle_deg, 1),
                    }
                    for candidate in primary_candidates[:2]
                ],
            }
        else:
            selected.append(best)
    # Pick one target per independent branch class. In particular, a
    # roundabout approach can see multiple ring segments inside 25m; connecting
    # all of them would create shortcuts across the circle.
    seen = {(movement.target.segment_id, movement.target.direction) for movement in selected}
    branch_groups: dict[tuple[str, str], list[MovementCandidate]] = {}
    for branch in branches:
        angle_bucket = (
            "left" if branch.angle_deg > 15 else "right" if branch.angle_deg < -15 else "through"
        )
        branch_groups.setdefault((branch.kind, angle_bucket), []).append(branch)
    for branch_group in branch_groups.values():
        branch_group.sort(key=lambda candidate: candidate.score)
        branch = branch_group[0]
        if (
            len(branch_group) > 1
            and branch_group[1].score[:3] == branch.score[:3]
            and abs(branch_group[1].angle_deg - branch.angle_deg)
            < AMBIGUOUS_ANGLE_DELTA_DEG
        ):
            continue
        key = (branch.target.segment_id, branch.target.direction)
        if key not in seen:
            selected.append(branch)
            seen.add(key)
    return selected, diagnostic


def _placement_widening_side(movement: MovementCandidate) -> str | None:
    source_anchor = _placement_anchor(movement.source, outgoing=True)
    target_anchor = _placement_anchor(movement.target, outgoing=False)
    if (
        source_anchor is None
        or target_anchor is None
        or source_anchor.kind == "transition"
        or target_anchor.kind == "transition"
    ):
        return None
    if (
        source_anchor.kind == target_anchor.kind
        and source_anchor.lane_nr == target_anchor.lane_nr
    ):
        return "anchored"
    return None


def _block_widening_side(
    source: Sequence[LaneTraversal],
    target: Sequence[LaneTraversal],
    movement: MovementCandidate,
) -> str | None:
    anchored = _placement_widening_side(movement)
    source_numbers = [lane.lane_nr for lane in source]
    target_numbers = [lane.lane_nr for lane in target]
    if target_numbers[: len(source_numbers)] == source_numbers:
        return "right"
    if target_numbers[-len(source_numbers) :] == source_numbers:
        return "left"
    if anchored:
        source_first = source_numbers[0]
        target_first = target_numbers[0]
        if target_first == source_first:
            return "right"
        if target_numbers[-1] == source_numbers[-1]:
            return "left"
    return None


def _destination_filtered_lanes(
    source: Sequence[LaneTraversal],
    target: TraversalGroup,
) -> list[LaneTraversal]:
    target_ref = str(target.representative.context.tags.get("ref") or "").strip().lower()
    target_name = str(
        target.representative.context.tags.get("name") or ""
    ).strip().lower()
    if not target_ref and not target_name:
        return list(source)
    matching = [
        lane
        for lane in source
        if (
            target_ref
            and target_ref in _lane_field_tokens(lane, "destination:ref:lanes")
        )
        or (
            target_name
            and target_name in _lane_field_tokens(lane, "destination:lanes")
        )
    ]
    return matching or list(source)


def _map_lane_blocks(
    source: Sequence[LaneTraversal],
    target: Sequence[LaneTraversal],
    movement: MovementCandidate,
) -> tuple[list[tuple[LaneTraversal, LaneTraversal]], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    if not source or not target:
        return [], diagnostics
    if len(source) == len(target):
        return list(zip(source, target)), diagnostics
    if len(source) > len(target):
        tagged_merge = _tagged_merge_assignments(source, target)
        if tagged_merge is not None:
            return tagged_merge, diagnostics
        anchored = _placement_widening_side(movement)
        if anchored:
            retained = list(zip(source[: len(target)], target))
            dropped = source[len(target) :]
            diagnostics.append(
                {
                    "reason": "unresolved_narrowing_merge",
                    "from_segment_id": movement.source.segment_id,
                    "to_segment_id": movement.target.segment_id,
                    "source_lanes": [lane.lane_nr for lane in source],
                    "target_lanes": [lane.lane_nr for lane in target],
                    "dropped_source_lanes": [lane.lane_nr for lane in dropped],
                    "survivor_evidence": "placement",
                }
            )
            return retained, diagnostics
        diagnostics.append(
            {
                "reason": "unresolved_narrowing_merge",
                "from_segment_id": movement.source.segment_id,
                "to_segment_id": movement.target.segment_id,
                "source_lanes": [lane.lane_nr for lane in source],
                "target_lanes": [lane.lane_nr for lane in target],
                "excess_lane_count": len(source) - len(target),
                "unresolved_source_lanes": [lane.lane_nr for lane in source],
            }
        )
        return [], diagnostics

    side = _block_widening_side(source, target, movement)
    if side is None:
        diagnostics.append(
            {
                "reason": "unresolved_widening_side",
                "from_segment_id": movement.source.segment_id,
                "to_segment_id": movement.target.segment_id,
                "source_lanes": [lane.lane_nr for lane in source],
                "target_lanes": [lane.lane_nr for lane in target],
            }
        )
        return list(zip(source, target[: len(source)])), diagnostics

    extra = len(target) - len(source)
    if side == "right":
        pairs = list(zip(source, target[: len(source)]))
        lateral = "right"
        split_source = source[-1]
        extra_targets = target[-extra:]
    else:
        pairs = list(zip(source, target[extra:]))
        lateral = "left"
        split_source = source[0]
        extra_targets = target[:extra]
    allowed = _lane_change_allowed(split_source, lateral)
    if allowed is False:
        diagnostics.append(
            {
                "reason": "change_lanes_conflict",
                "from": split_source.id,
                "lateral": lateral,
            }
        )
        return pairs, diagnostics
    pairs.extend((split_source, target_lane) for target_lane in extra_targets)
    return pairs, diagnostics


def _assignment_with_diagnostics(
    movement: MovementCandidate,
    *,
    source_lanes: Sequence[LaneTraversal] | None = None,
) -> tuple[list[tuple[LaneTraversal, LaneTraversal]], list[dict[str, Any]]]:
    source = list(movement.source.lanes if source_lanes is None else source_lanes)
    target = list(movement.target.lanes)
    if not source or not target:
        return [], []

    if movement.kind in {"exit", "tagged"}:
        matching = [
            lane
            for lane in source
            if any(
                turn_token_matches(token, movement.angle_deg)
                for token in _lane_turn_tokens(lane)
            )
        ]
        directional_tokens = (
            RIGHT_TURNING_TOKENS
            if movement.angle_deg < -5.0
            else LEFT_TURNING_TOKENS
            if movement.angle_deg > 5.0
            else set()
        )
        directional = [
            lane
            for lane in matching
            if any(
                token in directional_tokens
                and turn_token_matches(token, movement.angle_deg)
                for token in _lane_turn_tokens(lane)
            )
        ]
        # OSM turn ranges deliberately overlap (for example, a -20 degree
        # branch matches both through and slight_right). For an actual branch,
        # explicit directional tokens are stronger evidence than through.
        compatible = directional or matching
        selected = compatible or source[-min(len(source), len(target)) :]
        selected = _destination_filtered_lanes(selected, movement.target)
        return _map_lane_blocks(selected, target, movement)
    if movement.kind == "entry":
        count = min(len(source), len(target))
        return list(zip(source[:count], target[-count:])), []

    tagged_merge = _tagged_merge_assignments(source, target)
    if tagged_merge is not None:
        return tagged_merge, []
    return _map_lane_blocks(source, target, movement)


def assign_lanes(
    movement: MovementCandidate,
) -> list[tuple[LaneTraversal, LaneTraversal]]:
    """Create monotonic driver-left-to-right assignments for one movement."""
    pairs, _diagnostics = _assignment_with_diagnostics(movement)
    return pairs


def _classify_assignments(
    pairs: Sequence[tuple[LaneTraversal, LaneTraversal]],
) -> list[tuple[LaneTraversal, LaneTraversal, str]]:
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    result = []
    for source, target in sorted(
        pairs,
        key=lambda pair: (pair[0].lane_nr, pair[1].lane_nr),
    ):
        source_seen = source.id in seen_sources
        target_seen = target.id in seen_targets
        if source_seen and target_seen:
            # A single string connection_type cannot represent both without
            # losing information. Leave it unresolved in the first version.
            continue
        connection_type = "split" if source_seen else "join" if target_seen else "continuation"
        result.append((source, target, connection_type))
        seen_sources.add(source.id)
        seen_targets.add(target.id)
    return result


def _movement_group_key(
    movement: MovementCandidate,
) -> tuple[str, str, str, str]:
    return (
        movement.source.segment_id,
        movement.source.direction,
        movement.target.segment_id,
        movement.target.direction,
    )


def _source_block_lateral_offset(
    source_lanes: Sequence[LaneTraversal],
    target: TraversalGroup,
    lookback_m: float,
) -> float:
    """Return the source block centre's signed left offset from the target axis."""
    target_line = _group_source_geometry(target) or _group_geometry(target)
    target_start = target_line.coords[0]
    target_direction = unit_vector(target_line.coords[0], target_line.coords[1])
    reference = (
        target_start[0] - target_direction[0] * lookback_m,
        target_start[1] - target_direction[1] * lookback_m,
    )
    samples = []
    for lane in source_lanes:
        line = transform(_WGS84_TO_RD.transform, lane.line_wgs84)
        samples.append(line.interpolate(max(0.0, line.length - lookback_m)))
    centre_x = sum(point.x for point in samples) / len(samples)
    centre_y = sum(point.y for point in samples) / len(samples)
    delta_x = centre_x - reference[0]
    delta_y = centre_y - reference[1]
    return -target_direction[1] * delta_x + target_direction[0] * delta_y


def _resolve_multi_source_target_blocks(
    edges: Sequence[TopologyEdge],
    blocked: set[tuple[str, str]],
) -> tuple[list[TopologyEdge], list[dict[str, Any]], int]:
    """Allocate converging predecessor blocks jointly.

    Independent ``2 -> 4`` widening fallbacks are wrong when two exact
    two-lane predecessors jointly fill one four-lane target. Order complete
    source blocks by their measured lateral approach position and give each a
    contiguous target slice. Exact link handovers may participate up to 45
    degrees because a short ``placement=transition`` section often carries one
    side of the combined downstream cross-section.
    """
    by_target: dict[tuple[str, str], list[TopologyEdge]] = defaultdict(list)
    for edge in edges:
        by_target[(edge.target.segment_id, edge.target.direction)].append(edge)

    replaced_movements: set[tuple[str, str, str, str]] = set()
    replacements: list[TopologyEdge] = []
    diagnostics: list[dict[str, Any]] = []
    resolved_count = 0
    for target_key, target_edges in by_target.items():
        movement_by_source: dict[tuple[str, str], MovementCandidate] = {}
        source_lanes_by_group: dict[
            tuple[str, str], dict[str, LaneTraversal]
        ] = defaultdict(dict)
        valid = True
        for edge in target_edges:
            movement = edge.movement
            source_key = (movement.source.segment_id, movement.source.direction)
            existing = movement_by_source.get(source_key)
            if existing is not None and _movement_group_key(existing) != _movement_group_key(
                movement
            ):
                valid = False
                break
            movement_by_source[source_key] = movement
            source_lanes_by_group[source_key][edge.source.id] = edge.source
        if not valid or len(movement_by_source) < 2:
            continue

        movements = list(movement_by_source.values())
        target = movements[0].target
        if any(
            not movement.exact
            or movement.kind not in {"continuation", "entry", "exit"}
            or abs(movement.angle_deg)
            > (
                MULTI_SOURCE_LINK_BLOCK_MAX_ANGLE_DEG
                if (
                    movement.source.representative.context.is_link
                    or movement.target.representative.context.is_link
                )
                else MULTI_SOURCE_BLOCK_MAX_ANGLE_DEG
            )
            for movement in movements
        ):
            continue
        if any(
            len(source_lanes_by_group[source_key])
            != len(movement.source.lanes)
            for source_key, movement in movement_by_source.items()
        ):
            continue
        if sum(
            len(source_lanes_by_group[source_key])
            for source_key in movement_by_source
        ) != len(target.lanes):
            continue

        source_lengths = [
            transform(_WGS84_TO_RD.transform, lane.line_wgs84).length
            for lanes in source_lanes_by_group.values()
            for lane in lanes.values()
        ]
        target_line = _group_source_geometry(target) or _group_geometry(target)
        lookback_m = min(
            MULTI_SOURCE_BLOCK_LOOKBACK_M,
            target_line.length * 0.5,
            *(length * 0.5 for length in source_lengths),
        )
        if lookback_m < COUNT_TRANSITION_MIN_TRIM_M:
            continue

        ordered_blocks = []
        for source_key, movement in movement_by_source.items():
            source_lanes = sorted(
                source_lanes_by_group[source_key].values(),
                key=lambda lane: lane.lane_nr,
            )
            lateral_offset = _source_block_lateral_offset(
                source_lanes,
                target,
                lookback_m,
            )
            ordered_blocks.append(
                (lateral_offset, source_key, movement, source_lanes)
            )
        ordered_blocks.sort(key=lambda item: item[0], reverse=True)
        if any(
            left[0] - right[0] < MULTI_SOURCE_BLOCK_MIN_LATERAL_DELTA_M
            for left, right in zip(ordered_blocks, ordered_blocks[1:])
        ):
            diagnostics.append(
                {
                    "reason": "ambiguous_multi_source_lateral_order",
                    "target_segment_id": target_key[0],
                    "source_offsets_m": {
                        source_key[0]: round(offset, 2)
                        for offset, source_key, _movement, _lanes in ordered_blocks
                    },
                }
            )
            continue

        target_lanes = list(target.lanes)
        target_index = 0
        proposed: list[TopologyEdge] = []
        proposed_pairs: list[tuple[str, str]] = []
        allocation: list[dict[str, Any]] = []
        joint_trim_m = min(
            lookback_m,
            target_line.length * COUNT_TRANSITION_MAX_LINE_FRACTION,
            *(length * COUNT_TRANSITION_MAX_LINE_FRACTION for length in source_lengths),
        )
        for lateral_offset, source_key, movement, source_lanes in ordered_blocks:
            target_block = target_lanes[
                target_index : target_index + len(source_lanes)
            ]
            target_index += len(source_lanes)
            source_anchor = _placement_anchor(movement.source, outgoing=True)
            block_trim_m = (
                0.0
                if source_anchor is not None and source_anchor.kind == "transition"
                else joint_trim_m
            )
            proposed_pairs.extend(
                (source.id, target_lane.id)
                for source, target_lane in zip(source_lanes, target_block)
            )
            proposed.extend(
                TopologyEdge(
                    source,
                    target_lane,
                    movement,
                    "continuation",
                    minimum_trim_m=block_trim_m,
                    allocation_evidence="multi_source_lateral_order",
                )
                for source, target_lane in zip(source_lanes, target_block)
            )
            allocation.append(
                {
                    "source_segment_id": source_key[0],
                    "source_lanes": [lane.lane_nr for lane in source_lanes],
                    "target_lanes": [lane.lane_nr for lane in target_block],
                    "lateral_offset_m": round(lateral_offset, 2),
                }
            )
        if any(pair in blocked for pair in proposed_pairs):
            continue

        replaced_movements.update(
            _movement_group_key(movement) for movement in movements
        )
        replacements.extend(proposed)
        resolved_count += 1
        diagnostics.append(
            {
                "reason": "resolved_multi_source_lane_blocks",
                "target_segment_id": target_key[0],
                "lookback_m": round(lookback_m, 2),
                "trim_m": round(joint_trim_m, 2),
                "allocation": allocation,
            }
        )

    if not replaced_movements:
        return list(edges), diagnostics, resolved_count
    retained = [
        edge
        for edge in edges
        if _movement_group_key(edge.movement) not in replaced_movements
    ]
    return [*retained, *replacements], diagnostics, resolved_count


def _count_transition_trims(
    source: LaneTraversal,
    target: LaneTraversal,
    source_rd: LineString,
    target_rd: LineString,
    *,
    allow_wide_link_angle: bool = False,
) -> tuple[float, float]:
    """Return safe two-sided taper lengths for a near-straight lane transition."""
    start, end = source_rd.coords[-1], target_rd.coords[0]
    gap = Point(start).distance(Point(end))
    lateral_shift = _lateral_endpoint_shift_m(source_rd, target_rd)
    is_link_transition = source.context.is_link != target.context.is_link
    if (
        source.lane_count == target.lane_count
        and (
            gap < EQUAL_COUNT_TRANSITION_MIN_ENDPOINT_GAP_M
            or (
                source.context.is_oneway == target.context.is_oneway
                and lateral_shift < EQUAL_COUNT_TRANSITION_MIN_ENDPOINT_GAP_M
            )
        )
    ):
        return 0.0, 0.0
    if gap > COUNT_TRANSITION_MAX_ENDPOINT_GAP_M:
        return 0.0, 0.0
    source_bearing = bearing_deg(source_rd.coords[-2], source_rd.coords[-1])
    target_bearing = bearing_deg(target_rd.coords[0], target_rd.coords[1])
    maximum_trim_angle = (
        LINK_TRANSITION_MAX_TRIM_ANGLE_DEG
        if is_link_transition and allow_wide_link_angle
        else COUNT_TRANSITION_MAX_ANGLE_DEG
    )
    angle = abs(angle_delta_deg(source_bearing, target_bearing))
    if angle > maximum_trim_angle:
        return 0.0, 0.0
    is_exact_near_straight_link = (
        is_link_transition
        and allow_wide_link_angle
        and angle <= LINK_NEAR_STRAIGHT_MAX_ANGLE_DEG
    )
    if is_exact_near_straight_link:
        maximum_trim = LINK_NEAR_STRAIGHT_MAX_TRIM_M
        trim_per_gap = LINK_NEAR_STRAIGHT_TRIM_PER_GAP
    elif is_link_transition:
        maximum_trim = LINK_TRANSITION_MAX_TRIM_M
        trim_per_gap = COUNT_TRANSITION_TRIM_PER_GAP
    else:
        maximum_trim = COUNT_TRANSITION_MAX_TRIM_M
        trim_per_gap = COUNT_TRANSITION_TRIM_PER_GAP
    desired = min(
        maximum_trim,
        max(COUNT_TRANSITION_MIN_TRIM_M, gap * trim_per_gap),
    )
    source_fraction = COUNT_TRANSITION_MAX_LINE_FRACTION
    target_fraction = COUNT_TRANSITION_MAX_LINE_FRACTION
    if is_link_transition:
        # A link/mainline handover is itself the transition runway. Let its
        # link side contribute more than an ordinary road segment, including
        # short OSM link fragments. The global visible-length budget below
        # still retains at least 20 percent of every physical lane line.
        if source.context.is_link:
            source_fraction = LINK_TRANSITION_MAX_LINE_FRACTION
        if target.context.is_link:
            target_fraction = LINK_TRANSITION_MAX_LINE_FRACTION
    return (
        min(desired, source_rd.length * source_fraction),
        min(desired, target_rd.length * target_fraction),
    )


def _lateral_endpoint_shift_m(
    source_rd: LineString,
    target_rd: LineString,
) -> float:
    """Return endpoint displacement perpendicular to the mean travel tangent."""
    start = source_rd.coords[-1]
    end = target_rd.coords[0]
    source_direction = unit_vector(source_rd.coords[-2], start)
    target_direction = unit_vector(end, target_rd.coords[1])
    travel_direction = unit_vector(
        (0.0, 0.0),
        (
            source_direction[0] + target_direction[0],
            source_direction[1] + target_direction[1],
        ),
    )
    displacement = (end[0] - start[0], end[1] - start[1])
    return abs(
        displacement[0] * travel_direction[1]
        - displacement[1] * travel_direction[0]
    )


def _is_straight_direction_transition(
    source: LaneTraversal,
    target: LaneTraversal,
    source_rd: LineString,
    target_rd: LineString,
) -> bool:
    """Whether an equal-count lateral offset should use a straight taper."""
    if source.lane_count != target.lane_count:
        return False
    gap = Point(source_rd.coords[-1]).distance(Point(target_rd.coords[0]))
    if not (
        EQUAL_COUNT_TRANSITION_MIN_ENDPOINT_GAP_M
        <= gap
        <= COUNT_TRANSITION_MAX_ENDPOINT_GAP_M
    ):
        return False
    if (
        source.context.is_oneway == target.context.is_oneway
        and _lateral_endpoint_shift_m(source_rd, target_rd)
        < EQUAL_COUNT_TRANSITION_MIN_ENDPOINT_GAP_M
    ):
        return False
    source_bearing = bearing_deg(source_rd.coords[-2], source_rd.coords[-1])
    target_bearing = bearing_deg(target_rd.coords[0], target_rd.coords[1])
    return (
        abs(angle_delta_deg(source_bearing, target_bearing))
        <= COUNT_TRANSITION_MAX_ANGLE_DEG
    )


def _connector_geometry_with_trims(
    source: LaneTraversal,
    target: LaneTraversal,
    *,
    from_trim_m: float | None = None,
    to_trim_m: float | None = None,
    from_handle_m: float | None = None,
    to_handle_m: float | None = None,
) -> tuple[LineString | None, float, float]:
    source_rd = transform(_WGS84_TO_RD.transform, source.line_wgs84)
    target_rd = transform(_WGS84_TO_RD.transform, target.line_wgs84)
    if from_trim_m is None or to_trim_m is None:
        desired_from, desired_to = _count_transition_trims(
            source, target, source_rd, target_rd
        )
        from_trim_m = desired_from if from_trim_m is None else from_trim_m
        to_trim_m = desired_to if to_trim_m is None else to_trim_m
    source_visible = (
        substring(source_rd, 0.0, source_rd.length - from_trim_m)
        if from_trim_m
        else source_rd
    )
    target_visible = (
        substring(target_rd, to_trim_m, target_rd.length)
        if to_trim_m
        else target_rd
    )
    start, end = source_visible.coords[-1], target_visible.coords[0]
    if Point(start).distance(Point(end)) <= ENDPOINT_TOUCH_TOLERANCE_M:
        return None, from_trim_m, to_trim_m
    if _is_straight_direction_transition(source, target, source_rd, target_rd):
        return (
            transform(_RD_TO_WGS84.transform, LineString([start, end])),
            from_trim_m,
            to_trim_m,
        )
    start_direction = unit_vector(
        source_visible.coords[-2], source_visible.coords[-1]
    )
    end_direction = unit_vector(target_visible.coords[0], target_visible.coords[1])
    curve_rd = bounded_cubic_bezier(
        start,
        start_direction,
        end,
        end_direction,
        start_handle_m=from_handle_m,
        end_handle_m=to_handle_m,
    )
    return (
        transform(_RD_TO_WGS84.transform, curve_rd),
        from_trim_m,
        to_trim_m,
    )


def connector_geometry(source: LaneTraversal, target: LaneTraversal) -> LineString | None:
    """Build a connector from the source travel-exit to target travel-entry."""
    geometry, _from_trim_m, _to_trim_m = _connector_geometry_with_trims(source, target)
    return geometry


def _physical_endpoint_side(traversal: LaneTraversal, *, outgoing: bool) -> str:
    stored_in_travel_order = (
        traversal.stored_direction != "both" or traversal.direction == "fwd"
    )
    if outgoing:
        return "end" if stored_in_travel_order else "start"
    return "start" if stored_in_travel_order else "end"


def _resolve_trim_requests(
    edges: Sequence[TopologyEdge],
) -> tuple[
    dict[tuple[str, str], tuple[float, float]],
    dict[tuple[str, str], float],
    set[str],
]:
    """Resolve one shared trim per physical endpoint with a per-line budget."""
    requests_by_edge: dict[tuple[str, str], tuple[float, float]] = {}
    endpoint_requests: dict[tuple[str, str], float] = defaultdict(float)
    cross_section_endpoints: dict[
        tuple[str, str, str], set[tuple[str, str]]
    ] = defaultdict(set)
    lane_lengths: dict[str, float] = {}
    for edge in edges:
        source_rd = transform(_WGS84_TO_RD.transform, edge.source.line_wgs84)
        target_rd = transform(_WGS84_TO_RD.transform, edge.target.line_wgs84)
        requested_from, requested_to = _count_transition_trims(
            edge.source,
            edge.target,
            source_rd,
            target_rd,
            allow_wide_link_angle=edge.movement.exact,
        )
        if edge.minimum_trim_m:
            requested_from = max(requested_from, edge.minimum_trim_m)
            requested_to = max(requested_to, edge.minimum_trim_m)
        requests_by_edge[(edge.source.id, edge.target.id)] = (
            requested_from,
            requested_to,
        )
        from_key = (
            edge.source.lane_id,
            _physical_endpoint_side(edge.source, outgoing=True),
        )
        to_key = (
            edge.target.lane_id,
            _physical_endpoint_side(edge.target, outgoing=False),
        )
        endpoint_requests[from_key] = max(endpoint_requests[from_key], requested_from)
        endpoint_requests[to_key] = max(endpoint_requests[to_key], requested_to)
        cross_section_endpoints[
            (edge.source.segment_id, edge.source.direction, from_key[1])
        ].add(from_key)
        cross_section_endpoints[
            (edge.target.segment_id, edge.target.direction, to_key[1])
        ].add(to_key)
        lane_lengths[edge.source.lane_id] = source_rd.length
        lane_lengths[edge.target.lane_id] = target_rd.length

    # Keep a cross-section at one longitudinal station. Otherwise a long trim
    # on a newly added/merging edge can pass through an adjacent lane's much
    # shorter connector even though the lane mapping itself is monotonic.
    for endpoint_keys in cross_section_endpoints.values():
        positive_keys = {
            key for key in endpoint_keys if endpoint_requests.get(key, 0.0) > 0.0
        }
        shared_request = max(
            (endpoint_requests.get(key, 0.0) for key in positive_keys),
            default=0.0,
        )
        for key in positive_keys:
            endpoint_requests[key] = shared_request

    resolved = dict(endpoint_requests)
    scaled_lanes: set[str] = set()
    for lane_id, length_m in lane_lengths.items():
        start_key = (lane_id, "start")
        end_key = (lane_id, "end")
        start_request = endpoint_requests.get(start_key, 0.0)
        end_request = endpoint_requests.get(end_key, 0.0)
        requested_total = start_request + end_request
        minimum_visible = min(
            length_m,
            max(MINIMUM_VISIBLE_LENGTH_M, length_m * 0.2),
        )
        maximum_total = min(
            length_m * MAXIMUM_TOTAL_TRIM_FRACTION,
            max(0.0, length_m - minimum_visible),
        )
        if requested_total <= maximum_total or requested_total == 0.0:
            continue
        scale = maximum_total / requested_total
        resolved[start_key] = start_request * scale
        resolved[end_key] = end_request * scale
        scaled_lanes.add(lane_id)
    return requests_by_edge, resolved, scaled_lanes


def _resolve_connector_handles(
    edges: Sequence[TopologyEdge],
    resolved_trims: Mapping[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """Use one control-handle length for every shared physical endpoint."""
    requests: dict[tuple[str, str], list[float]] = defaultdict(list)
    for edge in edges:
        source_rd = transform(_WGS84_TO_RD.transform, edge.source.line_wgs84)
        target_rd = transform(_WGS84_TO_RD.transform, edge.target.line_wgs84)
        from_side = _physical_endpoint_side(edge.source, outgoing=True)
        to_side = _physical_endpoint_side(edge.target, outgoing=False)
        from_key = (edge.source.lane_id, from_side)
        to_key = (edge.target.lane_id, to_side)
        from_trim = resolved_trims.get(from_key, 0.0)
        to_trim = resolved_trims.get(to_key, 0.0)
        source_visible = (
            substring(source_rd, 0.0, source_rd.length - from_trim)
            if from_trim
            else source_rd
        )
        target_visible = (
            substring(target_rd, to_trim, target_rd.length)
            if to_trim
            else target_rd
        )
        span = Point(source_visible.coords[-1]).distance(
            Point(target_visible.coords[0])
        )
        desired = min(15.0, span * 0.45)
        requests[from_key].append(desired)
        requests[to_key].append(desired)
    return {key: min(values) for key, values in requests.items()}


def _connection_row(
    source: LaneTraversal,
    target: LaneTraversal,
    *,
    connection_type: str,
    confidence: str,
    from_trim_m: float | None = None,
    to_trim_m: float | None = None,
    from_handle_m: float | None = None,
    to_handle_m: float | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    geometry, from_trim_m, to_trim_m = _connector_geometry_with_trims(
        source,
        target,
        from_trim_m=from_trim_m,
        to_trim_m=to_trim_m,
        from_handle_m=from_handle_m,
        to_handle_m=to_handle_m,
    )
    if geometry is None:
        return None
    connection_id = f"{source.id}>{target.id}"
    return {
        "id": connection_id,
        "from_lane_id": source.lane_id,
        "from_direction": source.direction,
        "to_lane_id": target.lane_id,
        "to_direction": target.direction,
        "from_road_id": source.road_id,
        "to_road_id": target.road_id,
        "from_segment_id": source.segment_id,
        "to_segment_id": target.segment_id,
        "connection_type": connection_type,
        "confidence": confidence,
        "geom": geometry.wkt,
        "raw": {
            **dict(raw or {}),
            **(
                {
                    "from_trim_m": round(from_trim_m, 2),
                    "to_trim_m": round(to_trim_m, 2),
                }
                if from_trim_m or to_trim_m
                else {}
            ),
        },
    }


def load_connection_overrides(path: Path | str) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, list):
        raise ValueError("lane connection override file must contain a JSON list")
    return [dict(item) for item in value]


def normalize_traversal_reference(
    reference: str,
    traversals: Mapping[str, LaneTraversal],
) -> str:
    """Normalize a full lane/traversal ID or unambiguous segment:lane shorthand."""
    if reference in traversals:
        return reference
    if reference.startswith("ll:") and "@" not in reference:
        matches = [key for key in traversals if key.startswith(f"{reference}@")]
    elif not reference.startswith("ll:"):
        parts = reference.split(":")
        if len(parts) != 4:
            raise ValueError(f"invalid lane traversal reference: {reference}")
        segment_id, lane_nr = ":".join(parts[:3]), parts[3]
        matches = [
            key
            for key, traversal in traversals.items()
            if traversal.segment_id == segment_id and str(traversal.lane_nr) == lane_nr
        ]
    else:
        matches = []
    if len(matches) != 1:
        qualifier = "missing" if not matches else "ambiguous"
        raise ValueError(f"{qualifier} lane traversal reference: {reference}")
    return matches[0]


def build_lane_connections(
    lane_rows: Sequence[Mapping[str, Any]],
    road_contexts: Mapping[int, RoadContext | Mapping[str, Any]] | None = None,
    *,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Build automatic and manual connections plus unresolved diagnostics."""
    traversals = lane_traversals(lane_rows, road_contexts)
    by_id = {traversal.id: traversal for traversal in traversals}
    groups = _groups(traversals)
    missing_source_geometry = [
        group
        for group in groups
        if group.representative.source_line_wgs84 is None
    ]
    raw_discovered = discover_movement_candidates(groups)
    node_coordinate_conflicts = [
        candidate
        for candidates in raw_discovered.values()
        for candidate in candidates
        if candidate.adjacency_evidence == "junction_box"
        and candidate.distance_m <= SOURCE_ENDPOINT_EXACT_TOLERANCE_M
        and candidate.source.representative.exit_node_id
        and candidate.target.representative.entry_node_id
        and candidate.source.representative.exit_node_id
        != candidate.target.representative.entry_node_id
    ]
    discovered, dominance_diagnostics = suppress_dominated_candidates(
        raw_discovered
    )
    normalized_overrides = []
    for item in overrides:
        normalized = dict(item)
        normalized["from"] = normalize_traversal_reference(str(item.get("from")), by_id)
        normalized["to"] = normalize_traversal_reference(str(item.get("to")), by_id)
        normalized_overrides.append(normalized)
    blocked = {
        (str(item["from"]), str(item["to"]))
        for item in normalized_overrides
        if item.get("action") == "block"
    }
    rows: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = [
        *dominance_diagnostics,
        *(
            {
                "reason": "missing_source_geometry",
                "segment_id": group.segment_id,
                "direction": group.direction,
            }
            for group in missing_source_geometry
        ),
        *(
            {
                "reason": "node_coordinate_adjacency_conflict",
                "from": candidate.source.representative.id,
                "to": candidate.target.representative.id,
                "source_node_id": candidate.source.representative.exit_node_id,
                "target_node_id": candidate.target.representative.entry_node_id,
                "endpoint_distance_m": round(candidate.distance_m, 3),
            }
            for candidate in node_coordinate_conflicts
        ),
    ]
    counters = {
        "lane_endpoints_considered": len(groups),
        "exact_movements": 0,
        "node_exact_movements": 0,
        "endpoint_exact_movements": 0,
        "junction_box_movements": 0,
        "junction_box_suppressed_intermediate": sum(
            item.get("reason") == "intermediate_segment_dominates"
            for item in dominance_diagnostics
        ),
        "junction_box_rejected_existing_link_predecessor": sum(
            item.get("reason")
            == "existing_link_predecessor_rejects_new_exit"
            for item in dominance_diagnostics
        ),
        "junction_box_dominance_not_proven_no_successor": sum(
            item.get("reason") == "dominance_not_proven_no_successor"
            for item in dominance_diagnostics
        ),
        "missing_source_geometry": len(missing_source_geometry),
        "entry_movements": 0,
        "exit_movements": 0,
        "primary_ambiguities": 0,
        "node_coordinate_adjacency_conflicts": len(node_coordinate_conflicts),
        "lane_family_splits": 0,
        "lane_family_joins": 0,
        "multi_source_block_allocations": 0,
        "multi_source_block_ambiguities": 0,
        "pending_merge_continuations": 0,
        "placement_supported_widenings": 0,
        "destination_supported_allocations": 0,
        "unresolved_lane_family_mismatch": 0,
        "invalid_turn_lane_cardinality": 0,
        "invalid_destination_lane_cardinality": 0,
        "invalid_change_lane_cardinality": 0,
        "change_lane_conflicts": 0,
        "trim_budget_scaled": 0,
        "collapsed_short_transitions": 0,
        "manual_connects": 0,
        "manual_blocks": len(blocked),
        "connectors_omitted_touching": 0,
    }
    for group in groups:
        for base, counter_name in (
            ("turn:lanes", "invalid_turn_lane_cardinality"),
            ("destination:lanes", "invalid_destination_lane_cardinality"),
            ("destination:ref:lanes", "invalid_destination_lane_cardinality"),
            ("change:lanes", "invalid_change_lane_cardinality"),
        ):
            parsed = _parse_lane_fields(group, base)
            if parsed.key is None or parsed.valid:
                continue
            counters[counter_name] += 1
            diagnostics.append(
                {
                    "reason": "invalid_lane_tag_cardinality",
                    "from": group.representative.id,
                    "tag": parsed.key,
                    "raw": parsed.raw,
                    "expected_count": parsed.expected_count,
                    "actual_count": parsed.actual_count,
                }
            )

    topology_edges: list[TopologyEdge] = []
    for group in groups:
        key = (group.segment_id, group.direction)
        candidates = []
        for candidate in discovered.get(key, ()):
            candidate_pairs = assign_lanes(candidate)
            if candidate_pairs and all(
                (source.id, target.id) in blocked
                for source, target in candidate_pairs
            ):
                continue
            candidates.append(candidate)
        movements, diagnostic = choose_movement_set(candidates)
        if diagnostic:
            counters["primary_ambiguities"] += 1
            diagnostics.append(
                {
                    "node": list(group.representative.exit),
                    "node_id": group.representative.exit_node_id,
                    "from": group.representative.id,
                    **diagnostic,
                }
            )

        # Allocate explicitly tagged branches before the primary movement.
        # A lane carrying only a directional turn token belongs to that branch
        # and must not subsequently be consumed by the continuation fallback.
        # A combined token such as through;slight_right deliberately remains
        # available to both movements and produces a real split.
        assignment_results: list[
            tuple[
                MovementCandidate,
                list[tuple[LaneTraversal, LaneTraversal]],
                list[dict[str, Any]],
            ]
        ] = []
        branch_owned_source_ids: set[str] = set()
        for movement in movements:
            if movement.kind not in {"exit", "tagged"}:
                continue
            assignments, assignment_diagnostics = _assignment_with_diagnostics(
                movement
            )
            assignment_results.append(
                (movement, assignments, assignment_diagnostics)
            )
            for source, _target in assignments:
                tokens = _lane_turn_tokens(source)
                if tokens & TURNING_TOKENS and not tokens & PRIMARY_TOKENS:
                    branch_owned_source_ids.add(source.id)

        for movement in movements:
            if movement.kind in {"exit", "tagged"}:
                continue
            available_sources = [
                lane
                for lane in movement.source.lanes
                if lane.id not in branch_owned_source_ids
            ]
            assignments, assignment_diagnostics = _assignment_with_diagnostics(
                movement,
                source_lanes=available_sources,
            )
            assignment_results.append(
                (movement, assignments, assignment_diagnostics)
            )

        for movement, assignments, assignment_diagnostics in assignment_results:
            if movement.exact:
                counters["exact_movements"] += 1
                counters[f"{movement.adjacency_evidence}_movements"] += 1
            else:
                counters["junction_box_movements"] += 1
            if movement.kind == "entry":
                counters["entry_movements"] += 1
            elif movement.kind == "exit":
                counters["exit_movements"] += 1
            diagnostics.extend(assignment_diagnostics)
            counters["unresolved_lane_family_mismatch"] += sum(
                item.get("reason")
                in {"unresolved_widening_side", "unresolved_narrowing_merge"}
                for item in assignment_diagnostics
            )
            counters["change_lane_conflicts"] += sum(
                item.get("reason") == "change_lanes_conflict"
                for item in assignment_diagnostics
            )
            classified_assignments = _classify_assignments(assignments)
            if len(classified_assignments) != len(assignments):
                counters["unresolved_lane_family_mismatch"] += (
                    len(assignments) - len(classified_assignments)
                )
                diagnostics.append(
                    {
                        "reason": "unresolved_simultaneous_split_join",
                        "from_segment_id": movement.source.segment_id,
                        "to_segment_id": movement.target.segment_id,
                    }
                )
            for source, target, connection_type in classified_assignments:
                if (source.id, target.id) in blocked:
                    continue
                topology_edges.append(
                    TopologyEdge(source, target, movement, connection_type)
                )
                if (
                    _lane_field_tokens(source, "destination:lanes")
                    or _lane_field_tokens(source, "destination:ref:lanes")
                ):
                    counters["destination_supported_allocations"] += 1

    (
        topology_edges,
        multi_source_diagnostics,
        counters["multi_source_block_allocations"],
    ) = _resolve_multi_source_target_blocks(topology_edges, blocked)
    diagnostics.extend(multi_source_diagnostics)
    counters["multi_source_block_ambiguities"] = sum(
        item.get("reason") == "ambiguous_multi_source_lateral_order"
        for item in multi_source_diagnostics
    )

    # When a separate entry road supplies a newly added target lane, an
    # inferred mainline split into that same lane is redundant. Keeping both
    # produces a false weave: the mainline connector crosses the entry before
    # both arrive at the same target traversal.
    incoming_by_target: dict[str, list[TopologyEdge]] = defaultdict(list)
    for edge in topology_edges:
        incoming_by_target[edge.target.id].append(edge)
    redundant_edges = {
        (edge.source.id, edge.target.id)
        for incoming in incoming_by_target.values()
        if any(edge.movement.kind == "entry" for edge in incoming)
        for edge in incoming
        if edge.connection_type == "split" and edge.movement.kind != "entry"
    }
    if redundant_edges:
        topology_edges = [
            edge
            for edge in topology_edges
            if (edge.source.id, edge.target.id) not in redundant_edges
        ]
        diagnostics.extend(
            {
                "reason": "entry_claims_added_lane",
                "from": source_id,
                "to": target_id,
            }
            for source_id, target_id in sorted(redundant_edges)
        )

    counters["lane_family_splits"] = sum(
        edge.connection_type == "split" for edge in topology_edges
    )
    counters["lane_family_joins"] = sum(
        edge.connection_type == "join" for edge in topology_edges
    )
    counters["placement_supported_widenings"] = sum(
        edge.connection_type == "split" and bool(_placement_widening_side(edge.movement))
        for edge in topology_edges
    )

    requests_by_edge, resolved_trims, scaled_lanes = _resolve_trim_requests(
        topology_edges
    )
    resolved_handles = _resolve_connector_handles(topology_edges, resolved_trims)
    counters["trim_budget_scaled"] = len(scaled_lanes)
    for edge in topology_edges:
        source, target, movement = edge.source, edge.target, edge.movement
        from_side = _physical_endpoint_side(source, outgoing=True)
        to_side = _physical_endpoint_side(target, outgoing=False)
        from_trim_m = resolved_trims.get((source.lane_id, from_side), 0.0)
        to_trim_m = resolved_trims.get((target.lane_id, to_side), 0.0)
        from_handle_m = resolved_handles.get((source.lane_id, from_side))
        to_handle_m = resolved_handles.get((target.lane_id, to_side))
        requested_from, requested_to = requests_by_edge.get(
            (source.id, target.id),
            (0.0, 0.0),
        )
        turn_tokens = _lane_turn_tokens(source)
        merge_state = (
            "pending"
            if edge.connection_type == "continuation" and turn_tokens & MERGE_TOKENS
            else None
        )
        if merge_state:
            counters["pending_merge_continuations"] += 1
        source_placement = _placement_anchor(movement.source, outgoing=True)
        target_placement = _placement_anchor(movement.target, outgoing=False)
        row = _connection_row(
            source,
            target,
            connection_type=edge.connection_type,
            confidence="exact" if movement.exact else "junction_box",
            from_trim_m=from_trim_m,
            to_trim_m=to_trim_m,
            from_handle_m=from_handle_m,
            to_handle_m=to_handle_m,
            raw={
                "angle_deg": round(movement.angle_deg, 2),
                "distance_m": round(movement.distance_m, 2),
                "adjacency_evidence": movement.adjacency_evidence,
                "movement_type": (
                    "exit" if movement.kind == "tagged" else movement.kind
                ),
                "allocation_evidence": edge.allocation_evidence,
                "turn_lane": ";".join(sorted(turn_tokens)) if turn_tokens else None,
                "destination_lane": ";".join(
                    sorted(_lane_field_tokens(source, "destination:lanes"))
                )
                or None,
                "destination_ref_lane": ";".join(
                    sorted(_lane_field_tokens(source, "destination:ref:lanes"))
                )
                or None,
                "change_lane": ";".join(
                    sorted(_lane_field_tokens(source, "change:lanes"))
                )
                or None,
                "source_placement": (
                    source_placement.raw if source_placement is not None else None
                ),
                "target_placement": (
                    target_placement.raw if target_placement is not None else None
                ),
                "merge_state": merge_state,
                "requested_from_trim_m": round(requested_from, 2),
                "requested_to_trim_m": round(requested_to, 2),
                "from_handle_m": round(from_handle_m, 2)
                if from_handle_m is not None
                else None,
                "to_handle_m": round(to_handle_m, 2)
                if to_handle_m is not None
                else None,
                "trim_scaled": (
                    source.lane_id in scaled_lanes or target.lane_id in scaled_lanes
                ),
            },
        )
        if row is None:
            counters["connectors_omitted_touching"] += 1
        else:
            rows[row["id"]] = row

    for override in normalized_overrides:
        if override.get("action") != "connect":
            continue
        from_id, to_id = str(override.get("from")), str(override.get("to"))
        if from_id not in by_id or to_id not in by_id:
            raise ValueError(f"manual connection references missing traversal: {from_id}>{to_id}")
        row = _connection_row(
            by_id[from_id],
            by_id[to_id],
            connection_type="manual",
            confidence="manual",
            raw={"note": override.get("note")},
        )
        if row is not None:
            rows[row["id"]] = row
        counters["manual_connects"] += 1
    return sorted(rows.values(), key=lambda row: row["id"]), diagnostics, counters


def _mean_unit_vector(
    vectors: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    if not vectors:
        raise ValueError("cannot average an empty set of directions")
    x = sum(vector[0] for vector in vectors)
    y = sum(vector[1] for vector in vectors)
    return unit_vector((0.0, 0.0), (x, y))


def _endpoint_direction(
    traversal: LaneTraversal,
    *,
    outgoing: bool,
) -> tuple[float, float]:
    line = transform(_WGS84_TO_RD.transform, traversal.line_wgs84)
    if outgoing:
        return unit_vector(line.coords[-2], line.coords[-1])
    return unit_vector(line.coords[0], line.coords[1])


def _explicit_transition_anchor(
    traversal: LaneTraversal,
    *,
    outgoing: bool,
) -> tuple[float, float] | None:
    """Return a concrete placement:start/end lane point in travel order."""
    group = TraversalGroup(traversal.segment_id, traversal.direction, (traversal,))
    anchor = _placement_anchor(group, outgoing=outgoing)
    if anchor is None or anchor.kind == "transition" or anchor.lane_nr is None:
        return None
    if anchor.lane_nr > traversal.lane_count or traversal.source_line_wgs84 is None:
        return None
    anchor_center = {
        "right_of": 1.75,
        "middle_of": 0.0,
        "left_of": -1.75,
    }[anchor.kind]
    offset_m = anchor_center + (anchor.lane_nr - traversal.lane_nr) * 3.5
    source = transform(_WGS84_TO_RD.transform, traversal.source_line_wgs84)
    if outgoing:
        point = source.coords[-1]
        direction = unit_vector(source.coords[-2], source.coords[-1])
    else:
        point = source.coords[0]
        direction = unit_vector(source.coords[0], source.coords[1])
    return (
        point[0] - direction[1] * offset_m,
        point[1] + direction[0] * offset_m,
    )


def _transition_neighbor_is_resolved(
    traversal: LaneTraversal,
    *,
    outgoing: bool,
) -> bool:
    group = TraversalGroup(traversal.segment_id, traversal.direction, (traversal,))
    anchor = _placement_anchor(group, outgoing=outgoing)
    return anchor is None or anchor.kind != "transition"


def resolve_transition_lane_geometry(
    lane_rows: Sequence[Mapping[str, Any]],
    preliminary_connections: Sequence[Mapping[str, Any]],
    road_contexts: Mapping[int, RoadContext | Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
    """Resolve simple ``placement=transition`` ways from connected lane anchors.

    The placement proposal tells consumers to infer missing start/end placement
    from the preceding and following sections. Connection allocation is built
    first from source topology/tags; this pass then replaces only unambiguous,
    one-way, two-node transition lane geometry. Curved or ambiguous cases stay
    unchanged and are reported instead of guessed.
    """
    rows = [dict(row) for row in lane_rows]
    rows_by_id = {str(row["id"]): row for row in rows}
    traversals = lane_traversals(rows, road_contexts)
    by_id = {traversal.id: traversal for traversal in traversals}
    groups = _groups(traversals)
    incoming: dict[str, list[LaneTraversal]] = defaultdict(list)
    outgoing: dict[str, list[LaneTraversal]] = defaultdict(list)
    for connection in preliminary_connections:
        if connection.get("confidence") != "exact":
            continue
        source_id = (
            f"{connection['from_lane_id']}@{connection['from_direction']}"
        )
        target_id = f"{connection['to_lane_id']}@{connection['to_direction']}"
        source = by_id.get(source_id)
        target = by_id.get(target_id)
        if source is None or target is None:
            continue
        outgoing[source.id].append(target)
        incoming[target.id].append(source)

    # Perfectly touching lane pairs deliberately have no connector feature.
    # Recover those topology edges from their exact shared node and endpoint
    # coincidence so they can still anchor a placement transition.
    endpoint_rd = {
        traversal.id: transform(_WGS84_TO_RD.transform, traversal.line_wgs84)
        for traversal in traversals
    }
    for lane in traversals:
        if not incoming.get(lane.id):
            lane_start = Point(endpoint_rd[lane.id].coords[0])
            incoming[lane.id].extend(
                candidate
                for candidate in traversals
                if candidate.road_id != lane.road_id
                and candidate.exit_node_id
                and candidate.exit_node_id == lane.entry_node_id
                and Point(endpoint_rd[candidate.id].coords[-1]).distance(lane_start)
                <= ENDPOINT_TOUCH_TOLERANCE_M
            )
        if not outgoing.get(lane.id):
            lane_end = Point(endpoint_rd[lane.id].coords[-1])
            outgoing[lane.id].extend(
                candidate
                for candidate in traversals
                if candidate.road_id != lane.road_id
                and lane.exit_node_id
                and candidate.entry_node_id == lane.exit_node_id
                and Point(endpoint_rd[candidate.id].coords[0]).distance(lane_end)
                <= ENDPOINT_TOUCH_TOLERANCE_M
            )

    diagnostics: list[dict[str, Any]] = []
    resolved_road_ids: set[int] = set()
    for group in groups:
        context = group.representative.context
        if (
            not context.is_oneway
            or str(context.tags.get("placement", "")).strip().lower()
            != "transition"
        ):
            continue
        reason = None
        if any(
            lane.stored_direction == "both" or len(lane.line_wgs84.coords) != 2
            for lane in group.lanes
        ):
            reason = "non_simple_transition_geometry"

        anchors: list[
            tuple[
                LaneTraversal,
                tuple[float, float],
                tuple[float, float],
                tuple[float, float],
                tuple[float, float],
                str,
                str,
            ]
        ] = []
        start_neighbor_ids: set[str] = set()
        end_neighbor_ids: set[str] = set()
        if reason is None:
            for lane in group.lanes:
                predecessors = [
                    candidate
                    for candidate in incoming.get(lane.id, ())
                    if candidate.road_id != lane.road_id
                    and _transition_neighbor_is_resolved(candidate, outgoing=True)
                ]
                successors = [
                    candidate
                    for candidate in outgoing.get(lane.id, ())
                    if candidate.road_id != lane.road_id
                    and _transition_neighbor_is_resolved(candidate, outgoing=False)
                ]
                explicit_start = _explicit_transition_anchor(lane, outgoing=False)
                explicit_end = _explicit_transition_anchor(lane, outgoing=True)
                if explicit_start is None and len(predecessors) != 1:
                    reason = "ambiguous_start_anchor"
                    break
                if explicit_end is None and len(successors) != 1:
                    reason = "ambiguous_end_anchor"
                    break
                predecessor = predecessors[0] if len(predecessors) == 1 else None
                successor = successors[0] if len(successors) == 1 else None
                start = (
                    explicit_start
                    if explicit_start is not None
                    else tuple(
                        transform(
                            _WGS84_TO_RD.transform,
                            predecessor.line_wgs84,
                        ).coords[-1]
                    )
                )
                end = (
                    explicit_end
                    if explicit_end is not None
                    else tuple(
                        transform(
                            _WGS84_TO_RD.transform,
                            successor.line_wgs84,
                        ).coords[0]
                    )
                )
                start_direction = (
                    _endpoint_direction(predecessor, outgoing=True)
                    if predecessor is not None
                    else _endpoint_direction(lane, outgoing=False)
                )
                end_direction = (
                    _endpoint_direction(successor, outgoing=False)
                    if successor is not None
                    else _endpoint_direction(lane, outgoing=True)
                )
                start_ref = predecessor.id if predecessor is not None else "explicit"
                end_ref = successor.id if successor is not None else "explicit"
                start_neighbor_ids.add(
                    predecessor.segment_id if predecessor is not None else "explicit"
                )
                end_neighbor_ids.add(
                    successor.segment_id if successor is not None else "explicit"
                )
                anchors.append(
                    (
                        lane,
                        start,
                        end,
                        start_direction,
                        end_direction,
                        start_ref,
                        end_ref,
                    )
                )
        if reason is None and (
            len(start_neighbor_ids) > 1 or len(end_neighbor_ids) > 1
        ):
            reason = "mixed_neighbor_lane_blocks"

        generated: dict[str, LineString] = {}
        if reason is None:
            start_direction = _mean_unit_vector(
                [anchor[3] for anchor in anchors]
            )
            end_direction = _mean_unit_vector([anchor[4] for anchor in anchors])
            start_bearing = bearing_deg((0.0, 0.0), start_direction)
            end_bearing = bearing_deg((0.0, 0.0), end_direction)
            if (
                abs(angle_delta_deg(start_bearing, end_bearing))
                > TRANSITION_PLACEMENT_MAX_ANGLE_DEG
            ):
                reason = "transition_angle_too_large"
            else:
                spans = [
                    math.hypot(end[0] - start[0], end[1] - start[1])
                    for _lane, start, end, *_rest in anchors
                ]
                handle_m = min(15.0, (sum(spans) / len(spans)) * 0.45)
                samples = max(
                    12,
                    min(
                        TRANSITION_PLACEMENT_MAX_SAMPLES,
                        math.ceil(sum(spans) / len(spans)),
                    ),
                )
                for lane, start, end, *_rest in anchors:
                    original = transform(_WGS84_TO_RD.transform, lane.line_wgs84)
                    endpoint_shift = max(
                        Point(original.coords[0]).distance(Point(start)),
                        Point(original.coords[-1]).distance(Point(end)),
                    )
                    if endpoint_shift > TRANSITION_PLACEMENT_MAX_ENDPOINT_SHIFT_M:
                        reason = "transition_endpoint_shift_too_large"
                        break
                    curve = bounded_cubic_bezier(
                        start,
                        start_direction,
                        end,
                        end_direction,
                        samples=samples,
                        start_handle_m=handle_m,
                        end_handle_m=handle_m,
                    )
                    if not curve.is_simple:
                        reason = "transition_curve_not_simple"
                        break
                    generated[lane.id] = curve

        if reason is None:
            ordered = sorted(group.lanes, key=lambda lane: lane.lane_nr)
            for left, right in zip(ordered, ordered[1:]):
                left_line = generated[left.id]
                right_line = generated[right.id]
                distances = [
                    Point(a).distance(Point(b))
                    for a, b in zip(left_line.coords, right_line.coords)
                ]
                if any(
                    abs(distance - 3.5)
                    > TRANSITION_PLACEMENT_SPACING_TOLERANCE_M
                    for distance in distances
                ):
                    reason = "transition_lane_spacing_out_of_bounds"
                    break
            if reason is None and any(
                first.crosses(second)
                for first, second in combinations(generated.values(), 2)
            ):
                reason = "transition_lanes_cross"

        if reason is not None:
            diagnostics.append(
                {
                    "reason": "unresolved_transition_placement",
                    "detail": reason,
                    "road_id": context.road_id,
                    "segment_id": group.segment_id,
                    "direction": group.direction,
                }
            )
            continue

        anchor_by_lane = {anchor[0].id: anchor for anchor in anchors}
        for lane in group.lanes:
            row = rows_by_id[lane.lane_id]
            curve = generated[lane.id]
            anchor = anchor_by_lane[lane.id]
            original = transform(_WGS84_TO_RD.transform, lane.line_wgs84)
            raw = dict(row.get("raw") or {})
            raw.update(
                {
                    "transition_placement_resolved": True,
                    "transition_start_from": anchor[5],
                    "transition_end_to": anchor[6],
                    "transition_start_shift_m": round(
                        Point(original.coords[0]).distance(Point(curve.coords[0])),
                        2,
                    ),
                    "transition_end_shift_m": round(
                        Point(original.coords[-1]).distance(Point(curve.coords[-1])),
                        2,
                    ),
                }
            )
            row["geom"] = transform(_RD_TO_WGS84.transform, curve).wkt
            row["raw"] = raw
        resolved_road_ids.add(context.road_id)
        diagnostics.append(
            {
                "reason": "resolved_transition_placement",
                "road_id": context.road_id,
                "segment_id": group.segment_id,
                "direction": group.direction,
                "lane_count": len(group.lanes),
            }
        )
    return rows, diagnostics, resolved_road_ids


def build_lane_network(
    lane_rows: Sequence[Mapping[str, Any]],
    road_contexts: Mapping[int, RoadContext | Mapping[str, Any]] | None = None,
    *,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
    set[int],
]:
    """Build topology, resolve transition placement, then rebuild connectors."""
    preliminary, _preliminary_diagnostics, _preliminary_counts = (
        build_lane_connections(
            lane_rows,
            road_contexts,
            overrides=overrides,
        )
    )
    resolved_rows, transition_diagnostics, resolved_road_ids = (
        resolve_transition_lane_geometry(
            lane_rows,
            preliminary,
            road_contexts,
        )
    )
    connections, diagnostics, counters = build_lane_connections(
        resolved_rows,
        road_contexts,
        overrides=overrides,
    )
    counters["resolved_transition_placements"] = len(resolved_road_ids)
    counters["unresolved_transition_placements"] = sum(
        item.get("reason") == "unresolved_transition_placement"
        for item in transition_diagnostics
    )
    return (
        resolved_rows,
        connections,
        [*transition_diagnostics, *diagnostics],
        counters,
        resolved_road_ids,
    )
