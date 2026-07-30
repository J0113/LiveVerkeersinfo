"""Independent OSM driving-lane centerline planning and metric geometry.

This module consumes only an OSM road's source geometry, ordered node IDs and
raw tags. It intentionally has no dependency on ``osm_lanes`` or
the retired physical-lane implementation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform

LANE_SPACING_M = 3.5
MAX_LANES = 12
OFFSET_ENDPOINT_TOLERANCE_M = 0.5
OFFSET_MIN_LENGTH_RATIO = 0.5
OFFSET_MAX_LENGTH_RATIO = 2.0

_WGS84_TO_RD = Transformer.from_crs(4326, 28992, always_xy=True)
_RD_TO_WGS84 = Transformer.from_crs(28992, 4326, always_xy=True)

_ONEWAY_FWD = {"yes", "true", "1"}
_ONEWAY_DYNAMIC = {"reversible", "alternating"}
_PLACEMENT_KINDS = {"left_of", "middle_of", "right_of"}


@dataclass(frozen=True)
class LaneSpec:
    physical_lane_index: int
    direction: str
    lane_nr: int
    lane_count: int
    offset_m: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LanePlan:
    lanes: tuple[LaneSpec, ...]
    count_source: str
    oneway_source: str | None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def physical_lane_count(self) -> int:
        return len(self.lanes)


@dataclass(frozen=True)
class LogicalSegment:
    road_id: int
    segment_id: str
    start_node_id: int
    end_node_id: int
    line: LineString
    source_start_index: int
    source_end_index: int


class LaneGeometryError(ValueError):
    """An offset cannot safely represent the complete logical segment."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _positive_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not text or any(character in text for character in ".;,"):
            return None
        number = int(text)
    except (TypeError, ValueError):
        return None
    return number if 0 < number <= MAX_LANES else None


def _nonnegative_int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        if not text or any(character in text for character in ".;,"):
            return None
        number = int(text)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= MAX_LANES else None


def _raw_positive_int(value: Any) -> tuple[int | None, str | None]:
    number = _positive_int(value)
    if number is not None:
        return number, None
    if value is None:
        return None, None
    return None, str(value)


def _turn_lane_count(tags: dict[str, Any], key: str = "turn:lanes") -> int | None:
    value = tags.get(key)
    if not isinstance(value, str):
        return None
    fields = value.split("|")
    return len(fields) if fields and len(fields) <= MAX_LANES else None


def _offset(index: int, total: int) -> float:
    return ((total - 1) / 2.0 - index) * LANE_SPACING_M


def _unknown_specs(total: int) -> tuple[LaneSpec, ...]:
    return tuple(
        LaneSpec(index, "unknown", index + 1, total, _offset(index, total))
        for index in range(total)
    )


def _oneway_specs(total: int, direction: str) -> tuple[LaneSpec, ...]:
    specs = []
    for index in range(total):
        lane_nr = index + 1 if direction == "fwd" else total - index
        specs.append(LaneSpec(index, direction, lane_nr, total, _offset(index, total)))
    return tuple(specs)


def _parse_placement_anchor(value: Any) -> tuple[str, int] | None:
    text = str(value or "").strip().lower()
    if ":" not in text:
        return None
    kind, raw_lane_nr = text.split(":", 1)
    lane_nr = _positive_int(raw_lane_nr)
    if kind not in _PLACEMENT_KINDS or lane_nr is None:
        return None
    return kind, lane_nr


def _placement_aligned_specs(
    specs: tuple[LaneSpec, ...],
    direction: str,
    tags: dict[str, Any],
    diagnostics: dict[str, Any],
) -> tuple[LaneSpec, ...]:
    """Align a one-way cross-section to a concrete OSM placement anchor."""
    directional_key = (
        "placement:forward" if direction == "fwd" else "placement:backward"
    )
    candidates = (
        (directional_key, tags.get(directional_key)),
        ("placement", tags.get("placement")),
    )
    selected_key = None
    selected_value = None
    anchor = None
    for key, value in candidates:
        if value is None:
            continue
        parsed = _parse_placement_anchor(value)
        if parsed is not None:
            selected_key, selected_value, anchor = key, str(value), parsed
            break
        if str(value).strip().lower() != "transition":
            diagnostics.setdefault("rejected_placement", {})[key] = str(value)

    # A constant endpoint-specific placement is safe to apply to the complete
    # way. Different start/end anchors require a variable-offset taper and are
    # intentionally left centered for now.
    if anchor is None:
        start_value = tags.get("placement:start")
        end_value = tags.get("placement:end")
        if start_value is not None and str(start_value) == str(end_value):
            parsed = _parse_placement_anchor(start_value)
            if parsed is not None:
                selected_key = "placement:start+end"
                selected_value = str(start_value)
                anchor = parsed
    if anchor is None:
        return specs

    kind, anchor_lane_nr = anchor
    lane_count = specs[0].lane_count if specs else 0
    if anchor_lane_nr > lane_count:
        diagnostics.setdefault("rejected_placement", {})[
            selected_key or "placement"
        ] = selected_value
        diagnostics["placement_lane_count"] = lane_count
        return specs

    # Placement is driver-relative. Positive metric offsets are left of the
    # stored OSM way, so backward travel reverses the sign.
    travel_sign = 1.0 if direction == "fwd" else -1.0
    anchor_center = {
        "right_of": LANE_SPACING_M / 2.0,
        "middle_of": 0.0,
        "left_of": -LANE_SPACING_M / 2.0,
    }[kind]
    aligned = []
    for spec in specs:
        driver_relative_offset = (
            anchor_center + (anchor_lane_nr - spec.lane_nr) * LANE_SPACING_M
        )
        aligned.append(
            replace(
                spec,
                offset_m=travel_sign * driver_relative_offset,
                raw={
                    **spec.raw,
                    "placement": selected_value,
                    "placement_key": selected_key,
                },
            )
        )
    diagnostics["placement"] = selected_value
    diagnostics["placement_key"] = selected_key
    return tuple(aligned)


def _directional_specs(backward: int, both_ways: int, forward: int) -> tuple[LaneSpec, ...]:
    total = backward + both_ways + forward
    specs: list[LaneSpec] = []
    for local_index in range(backward):
        physical_index = local_index
        specs.append(
            LaneSpec(
                physical_index,
                "bwd",
                backward - local_index,
                backward,
                _offset(physical_index, total),
            )
        )
    for local_index in range(both_ways):
        physical_index = backward + local_index
        specs.append(
            LaneSpec(
                physical_index,
                "unknown",
                local_index + 1,
                both_ways,
                _offset(physical_index, total),
                {"both_ways": True},
            )
        )
    for local_index in range(forward):
        physical_index = backward + both_ways + local_index
        specs.append(
            LaneSpec(
                physical_index,
                "fwd",
                local_index + 1,
                forward,
                _offset(physical_index, total),
            )
        )
    return tuple(specs)


def plan_lane_cross_section(tags: dict[str, Any] | None) -> LanePlan:
    """Interpret OSM tags into a stable physical cross-section."""
    tags = dict(tags or {})
    diagnostics: dict[str, Any] = {}
    raw_oneway = str(tags.get("oneway", "")).strip().lower()
    junction = str(tags.get("junction", "")).strip().lower()

    if raw_oneway in _ONEWAY_FWD:
        one_way, direction, oneway_source = True, "fwd", "tag"
    elif raw_oneway == "-1":
        one_way, direction, oneway_source = True, "bwd", "tag"
    elif raw_oneway in _ONEWAY_DYNAMIC:
        one_way, direction, oneway_source = False, "unknown", "tag"
        diagnostics["dynamic_oneway"] = raw_oneway
    elif raw_oneway == "no":
        one_way, direction, oneway_source = False, None, "tag"
        if junction == "roundabout":
            diagnostics["roundabout_oneway_anomaly"] = True
    elif junction == "roundabout":
        one_way, direction, oneway_source = True, "fwd", "roundabout_implied"
    else:
        one_way, direction, oneway_source = False, None, None

    total, rejected_total = _raw_positive_int(tags.get("lanes"))
    if rejected_total is not None:
        diagnostics["rejected_lanes"] = rejected_total
        try:
            if int(rejected_total) > MAX_LANES:
                diagnostics["over_ceiling"] = True
                return LanePlan((), "assumed", oneway_source, diagnostics)
        except ValueError:
            pass

    if raw_oneway in _ONEWAY_DYNAMIC:
        if total is None:
            total = 1
            count_source = "assumed"
        else:
            count_source = "lanes"
        return LanePlan(_unknown_specs(total), count_source, oneway_source, diagnostics)

    if one_way:
        turn_count = _turn_lane_count(tags)
        if total is not None:
            count_source = "lanes"
        elif turn_count is not None:
            total, count_source = turn_count, "turn_lanes"
        else:
            total, count_source = 1, "assumed"
        resolved_direction = direction or "fwd"
        specs = _placement_aligned_specs(
            _oneway_specs(total, resolved_direction),
            resolved_direction,
            tags,
            diagnostics,
        )
        return LanePlan(
            specs,
            count_source,
            oneway_source,
            diagnostics,
        )

    forward = _nonnegative_int(tags.get("lanes:forward"))
    backward = _nonnegative_int(tags.get("lanes:backward"))
    both_value = tags.get("lanes:both_ways")
    both_ways = _nonnegative_int(both_value) if both_value is not None else 0
    has_directional = any(
        key in tags for key in ("lanes:forward", "lanes:backward", "lanes:both_ways")
    )
    invalid_directional = any(
        key in tags and _nonnegative_int(tags.get(key)) is None
        for key in ("lanes:forward", "lanes:backward", "lanes:both_ways")
    )
    if invalid_directional:
        diagnostics["rejected_directional_tags"] = {
            key: tags.get(key)
            for key in ("lanes:forward", "lanes:backward", "lanes:both_ways")
            if key in tags and _nonnegative_int(tags.get(key)) is None
        }

    if has_directional and not invalid_directional:
        known_sum = (forward or 0) + (backward or 0) + both_ways
        if total is not None:
            if forward is None and backward is not None:
                forward = total - backward - both_ways
            elif backward is None and forward is not None:
                backward = total - forward - both_ways
            if forward is not None and backward is not None:
                if forward >= 0 and backward >= 0 and forward + backward + both_ways == total:
                    return LanePlan(
                        _directional_specs(backward, both_ways, forward),
                        "directional_tags",
                        oneway_source,
                        diagnostics,
                    )
                diagnostics["directional_sum"] = known_sum
                diagnostics["lanes_total"] = total
                return LanePlan(_unknown_specs(total), "conflict", oneway_source, diagnostics)
            diagnostics["directional_sum"] = known_sum
            diagnostics["lanes_total"] = total
            return LanePlan(_unknown_specs(total), "conflict", oneway_source, diagnostics)
        elif forward is not None and backward is not None:
            computed = forward + backward + both_ways
            if 0 < computed <= MAX_LANES:
                return LanePlan(
                    _directional_specs(backward, both_ways, forward),
                    "directional_tags",
                    oneway_source,
                    diagnostics,
                )
        elif (
            total is None
            and forward is None
            and backward is None
            and both_ways > 0
        ):
            diagnostics["incomplete_directional_tags"] = ["lanes:both_ways"]
            diagnostics["directional_sum"] = both_ways
            diagnostics["lanes_total"] = None
            return LanePlan(
                _directional_specs(0, both_ways, 0),
                "conflict",
                oneway_source,
                diagnostics,
            )

    if total is None:
        total, count_source = 2, "assumed"
    else:
        count_source = "lanes"
    if total == 1:
        return LanePlan(
            (LaneSpec(0, "both", 1, 1, 0.0),),
            count_source,
            oneway_source,
            diagnostics,
        )
    if total % 2:
        return LanePlan(_unknown_specs(total), count_source, oneway_source, diagnostics)
    half = total // 2
    return LanePlan(
        _directional_specs(half, 0, half),
        count_source,
        oneway_source,
        diagnostics,
    )


def topology_node_ids(
    node_ref_lists: Iterable[Sequence[int] | None],
) -> set[int]:
    """Return node IDs used by more than one retained OSM road."""
    per_way = Counter()
    for refs in node_ref_lists:
        per_way.update(set(refs or ()))
    return {node_id for node_id, count in per_way.items() if count > 1}


def split_logical_segments(
    road_id: int,
    line: LineString,
    node_refs: Sequence[int] | None,
    shared_node_ids: set[int] | None = None,
) -> list[LogicalSegment]:
    """Split an OSM way at endpoints, shared nodes, and repeated nodes."""
    coordinates = list(line.coords)
    if not node_refs or len(node_refs) != len(coordinates):
        return [LogicalSegment(road_id, f"{road_id}:0:0", 0, 0, line, 0, len(coordinates) - 1)]
    refs = list(node_refs)
    repeated = {node for node, count in Counter(refs).items() if count > 1}
    shared = set(shared_node_ids or ())
    closed = refs[0] == refs[-1]

    if closed:
        attachment_indexes = sorted(
            index
            for index, node_id in enumerate(refs[:-1])
            if node_id in shared
        )
        if not attachment_indexes:
            segment_id = f"{road_id}:{refs[0]}:{refs[-1]}"
            return [
                LogicalSegment(
                    road_id, segment_id, refs[0], refs[-1], line, 0, len(coordinates) - 1
                )
            ]
        segments: list[LogicalSegment] = []
        ring_size = len(refs) - 1
        for position, start_index in enumerate(attachment_indexes):
            end_index = attachment_indexes[(position + 1) % len(attachment_indexes)]
            if end_index <= start_index:
                indexes = list(range(start_index, ring_size + 1)) + list(range(1, end_index + 1))
            else:
                indexes = list(range(start_index, end_index + 1))
            segment_line = LineString([coordinates[index] for index in indexes])
            start_node, end_node = refs[start_index], refs[end_index]
            segments.append(
                LogicalSegment(
                    road_id,
                    f"{road_id}:{start_node}:{end_node}",
                    start_node,
                    end_node,
                    segment_line,
                    start_index,
                    end_index,
                )
            )
    else:
        split_indexes = sorted(
            {0, len(refs) - 1}
            | {
                index
                for index, node_id in enumerate(refs)
                if node_id in shared or node_id in repeated
            }
        )
        segments = []
        for start_index, end_index in zip(split_indexes, split_indexes[1:]):
            if end_index <= start_index:
                continue
            start_node, end_node = refs[start_index], refs[end_index]
            segments.append(
                LogicalSegment(
                    road_id,
                    f"{road_id}:{start_node}:{end_node}",
                    start_node,
                    end_node,
                    LineString(coordinates[start_index : end_index + 1]),
                    start_index,
                    end_index,
                )
            )
    ids = [segment.segment_id for segment in segments]
    if len(ids) != len(set(ids)):
        raise ValueError(f"road {road_id} produced duplicate logical segment IDs")
    return segments


def _expected_offset_endpoint(
    coordinates: Sequence[Sequence[float]], offset_m: float, *, start: bool
) -> tuple[float, float]:
    if start:
        anchor, neighbor = coordinates[0], coordinates[1]
        dx, dy = neighbor[0] - anchor[0], neighbor[1] - anchor[1]
    else:
        anchor, neighbor = coordinates[-1], coordinates[-2]
        dx, dy = anchor[0] - neighbor[0], anchor[1] - neighbor[1]
    length = (dx * dx + dy * dy) ** 0.5
    if not length:
        raise LaneGeometryError("degenerate_offset", "source has a zero-length end segment")
    return anchor[0] - dy / length * offset_m, anchor[1] + dx / length * offset_m


def offset_lane_geometry(source_wgs84: LineString, offset_m: float) -> LineString:
    """Offset one complete WGS84 segment in RD and validate its endpoints."""
    source_rd = transform(_WGS84_TO_RD.transform, source_wgs84)
    if offset_m == 0:
        result_rd = source_rd
    else:
        result_rd = source_rd.offset_curve(offset_m)
    if result_rd.is_empty or result_rd.geom_type != "LineString":
        raise LaneGeometryError(
            "empty_or_multipart",
            f"offset returned {result_rd.geom_type if not result_rd.is_empty else 'empty'}",
        )
    source_coords = list(source_rd.coords)
    result_coords = list(result_rd.coords)
    ratio = result_rd.length / source_rd.length if source_rd.length else 0.0
    if source_rd.is_ring:
        if not result_rd.is_ring or not OFFSET_MIN_LENGTH_RATIO <= ratio <= OFFSET_MAX_LENGTH_RATIO:
            raise LaneGeometryError(
                "degenerate_offset",
                f"closed offset is_open={not result_rd.is_ring}; length ratio {ratio:.3f}",
            )
        return transform(_RD_TO_WGS84.transform, result_rd)
    expected_start = _expected_offset_endpoint(source_coords, offset_m, start=True)
    expected_end = _expected_offset_endpoint(source_coords, offset_m, start=False)
    start_gap = Point(result_coords[0]).distance(Point(expected_start))
    end_gap = Point(result_coords[-1]).distance(Point(expected_end))
    if (
        start_gap > OFFSET_ENDPOINT_TOLERANCE_M
        or end_gap > OFFSET_ENDPOINT_TOLERANCE_M
        or not OFFSET_MIN_LENGTH_RATIO <= ratio <= OFFSET_MAX_LENGTH_RATIO
    ):
        raise LaneGeometryError(
            "degenerate_offset",
            f"offset endpoints moved {start_gap:.2f}m/{end_gap:.2f}m; length ratio {ratio:.3f}",
        )
    return transform(_RD_TO_WGS84.transform, result_rd)


def make_lane_line_rows(
    road_id: int,
    highway: str | None,
    tags: dict[str, Any] | None,
    line: LineString,
    *,
    node_refs: Sequence[int] | None = None,
    shared_node_ids: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build persistence-ready rows plus per-lane diagnostics."""
    tags = dict(tags or {})
    if str(tags.get("access", "")).strip().lower() == "no":
        return [], []

    plan = plan_lane_cross_section(tags)
    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for segment in split_logical_segments(road_id, line, node_refs, shared_node_ids):
        for lane in plan.lanes:
            try:
                geometry = offset_lane_geometry(segment.line, lane.offset_m)
            except LaneGeometryError as error:
                diagnostics.append(
                    {
                        "road_id": road_id,
                        "segment_id": segment.segment_id,
                        "lane_nr": lane.lane_nr,
                        "reason": error.reason,
                        "detail": str(error),
                    }
                )
                continue
            if lane.direction == "bwd":
                geometry = LineString(list(geometry.coords)[::-1])
            lane_id = f"ll:{segment.segment_id}:{lane.direction}:{lane.lane_nr}"
            rows.append(
                {
                    "id": lane_id,
                    "road_id": road_id,
                    "segment_id": segment.segment_id,
                    "lane_nr": lane.lane_nr,
                    "lane_count": lane.lane_count,
                    "physical_lane_index": lane.physical_lane_index,
                    "direction": lane.direction,
                    "offset_m": lane.offset_m,
                    "count_source": plan.count_source,
                    "oneway_source": plan.oneway_source,
                    "geom": geometry.wkt,
                    "raw": {
                        **lane.raw,
                        **plan.diagnostics,
                        "highway": highway,
                        "start_node_id": segment.start_node_id,
                        "end_node_id": segment.end_node_id,
                        "source_start": list(segment.line.coords[0]),
                        "source_end": list(segment.line.coords[-1]),
                    },
                }
            )
    return rows, diagnostics
