"""Bounded direction-aware traversal of the active OSM road graph.

The traversal deliberately follows persisted directed endpoints instead of
geometry.  A nearby line, fly-over or tunnel is therefore unreachable unless
OSM represents an actual shared graph node.  Database access stays bounded:
every lookup uses the graph/version endpoint indexes and reads at most
``branch_limit + 1`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select

from ndwinfo.models import OsmRoadSegment


@dataclass(frozen=True, slots=True)
class GraphSegment:
    internal_segment_id: str
    from_node_id: str
    to_node_id: str
    length_m: float


@dataclass(frozen=True, slots=True)
class SegmentLookup:
    segments: tuple[GraphSegment, ...]
    truncated: bool = False


class GraphSegmentProvider(Protocol):
    def get_segment(self, internal_segment_id: str) -> GraphSegment | None: ...

    def get_outgoing(self, from_node_id: str, limit: int) -> SegmentLookup: ...

    def get_incoming(self, to_node_id: str, limit: int) -> SegmentLookup: ...


class SqlGraphSegmentProvider:
    """Index-backed provider restricted to one immutable import run."""

    _columns = (
        OsmRoadSegment.internal_segment_id,
        OsmRoadSegment.from_node_id,
        OsmRoadSegment.to_node_id,
        OsmRoadSegment.length_m,
    )

    def __init__(self, db, *, import_run_id: int, graph_version: str):
        self.db = db
        self.import_run_id = import_run_id
        self.graph_version = graph_version
        self.lookup_count = 0
        self.candidate_rows_loaded = 0
        self._outgoing_cache: dict[tuple[str, int], SegmentLookup] = {}
        self._incoming_cache: dict[tuple[str, int], SegmentLookup] = {}

    def get_segment(self, internal_segment_id: str) -> GraphSegment | None:
        row = self.db.execute(
            select(*self._columns)
            .where(
                OsmRoadSegment.import_run_id == self.import_run_id,
                OsmRoadSegment.graph_version == self.graph_version,
                OsmRoadSegment.internal_segment_id == internal_segment_id,
            )
            .limit(1)
        ).first()
        self.lookup_count += 1
        self.candidate_rows_loaded += int(row is not None)
        return _to_segment(row) if row is not None else None

    def get_outgoing(self, from_node_id: str, limit: int) -> SegmentLookup:
        key = (from_node_id, limit)
        if key not in self._outgoing_cache:
            self._outgoing_cache[key] = self._endpoint_lookup(
                OsmRoadSegment.from_node_id,
                from_node_id,
                limit,
            )
        return self._outgoing_cache[key]

    def get_incoming(self, to_node_id: str, limit: int) -> SegmentLookup:
        key = (to_node_id, limit)
        if key not in self._incoming_cache:
            self._incoming_cache[key] = self._endpoint_lookup(
                OsmRoadSegment.to_node_id,
                to_node_id,
                limit,
            )
        return self._incoming_cache[key]

    def _endpoint_lookup(self, endpoint_column, node_id: str, limit: int) -> SegmentLookup:
        rows = self.db.execute(
            select(*self._columns)
            .where(
                OsmRoadSegment.import_run_id == self.import_run_id,
                OsmRoadSegment.graph_version == self.graph_version,
                endpoint_column == node_id,
            )
            .order_by(OsmRoadSegment.internal_segment_id)
            .limit(limit + 1)
        ).all()
        self.lookup_count += 1
        self.candidate_rows_loaded += len(rows)
        return SegmentLookup(
            tuple(_to_segment(row) for row in rows[:limit]),
            truncated=len(rows) > limit,
        )


@dataclass(frozen=True, slots=True)
class BranchPath:
    segment_ids: tuple[str, ...]
    distance_m: float
    terminal_reason: str

    def as_dict(self) -> dict:
        return {
            "segment_ids": list(self.segment_ids),
            "distance_m": round(self.distance_m, 1),
            "terminal_reason": self.terminal_reason,
        }


@dataclass(frozen=True, slots=True)
class RelevantPath:
    under: str
    behind: tuple[str, ...]
    common_ahead: tuple[str, ...]
    branches: tuple[BranchPath, ...]
    behind_distance_m: float
    common_ahead_distance_m: float
    branch_confidence: float
    truncated: bool
    terminal_reason: str

    @property
    def all_segment_ids(self) -> tuple[str, ...]:
        ordered = [self.under, *self.common_ahead]
        for branch in self.branches:
            ordered.extend(branch.segment_ids)
        ordered.extend(self.behind)
        # Preserve path order while de-duplicating branch merges.
        return tuple(dict.fromkeys(ordered))

    def as_dict(self) -> dict:
        visited_segment_count = len(self.all_segment_ids)
        limit_reached = self.truncated or self.terminal_reason in {
            "distance_limit",
            "edge_limit",
        } or any(
            branch.terminal_reason in {"distance_limit", "edge_limit"}
            for branch in self.branches
        )
        return {
            "under": self.under,
            "behind": list(self.behind),
            "common_ahead": list(self.common_ahead),
            "branches": [branch.as_dict() for branch in self.branches],
            "behind_distance_m": round(self.behind_distance_m, 1),
            "common_ahead_distance_m": round(self.common_ahead_distance_m, 1),
            # No route-choice evidence is available at a fork.  Zero is
            # intentionally fail-closed: branch-specific traffic must not be
            # presented as applicable until later GPS fixes select a branch.
            "branch_confidence": self.branch_confidence,
            "visited_segment_count": visited_segment_count,
            "limit_reached": limit_reached,
            "truncated": self.truncated,
            "terminal_reason": self.terminal_reason,
        }


class SegmentNotFoundError(LookupError):
    pass


def find_relevant_path(
    provider: GraphSegmentProvider,
    internal_segment_id: str,
    *,
    ahead_m: float,
    behind_m: float,
    max_edges: int,
    branch_limit: int,
) -> RelevantPath:
    """Return the connected path around a directed matched segment.

    ``common_ahead`` stops at the first unresolved fork.  Each returned branch
    then follows only its unambiguous continuation and stops at a subsequent
    fork.  ``behind`` likewise stops at a merge.  These conservative rules make
    the common path the only safe scope for live traffic before route choice is
    confirmed by later GPS fixes.
    """
    if ahead_m < 0 or behind_m < 0:
        raise ValueError("Traversal distances must be non-negative")
    if max_edges < 1 or branch_limit < 1:
        raise ValueError("Traversal bounds must be positive")

    start = provider.get_segment(internal_segment_id)
    if start is None:
        raise SegmentNotFoundError(internal_segment_id)

    admitted = {start.internal_segment_id}
    common: list[str] = []
    common_distance = 0.0
    truncated = False
    terminal_reason = "distance_limit" if ahead_m == 0 else "dead_end"
    visited_nodes = {start.from_node_id, start.to_node_id}
    current_node = start.to_node_id
    fork_candidates: tuple[GraphSegment, ...] = ()
    fork_detected = False

    while common_distance < ahead_m and len(admitted) < max_edges:
        lookup = provider.get_outgoing(current_node, branch_limit)
        truncated |= lookup.truncated
        candidates = _forward_candidates(lookup.segments, admitted, visited_nodes)
        if lookup.truncated:
            # Even when the bounded rows happen to contain only a U-turn, an
            # omitted candidate can still be a valid continuation.  Treat the
            # route as unresolved rather than claiming a dead end.
            fork_candidates = candidates
            fork_detected = True
            terminal_reason = "fork"
            break
        if not candidates:
            terminal_reason = "dead_end"
            break
        if len(candidates) > 1:
            fork_candidates = candidates
            fork_detected = True
            terminal_reason = "fork"
            break
        edge = candidates[0]
        if common_distance + edge.length_m > ahead_m:
            terminal_reason = "distance_limit"
            break
        _admit(edge, admitted, visited_nodes)
        common.append(edge.internal_segment_id)
        common_distance += edge.length_m
        current_node = edge.to_node_id
    else:
        if len(admitted) >= max_edges and common_distance < ahead_m:
            truncated = True
            terminal_reason = "edge_limit"
        else:
            terminal_reason = "distance_limit"

    branches: list[BranchPath] = []
    branch_distance_limit = max(0.0, ahead_m - common_distance)
    if fork_candidates:
        for first_edge in fork_candidates:
            if len(admitted) >= max_edges:
                truncated = True
                break
            path, distance, reason, branch_truncated = _follow_branch(
                provider,
                first_edge,
                admitted=admitted,
                base_visited_nodes=visited_nodes,
                distance_limit=branch_distance_limit,
                max_edges=max_edges,
                branch_limit=branch_limit,
            )
            truncated |= branch_truncated
            if path:
                branches.append(BranchPath(tuple(path), distance, reason))

    behind: list[str] = []
    behind_distance = 0.0
    behind_nodes = {start.to_node_id, start.from_node_id}
    behind_node = start.from_node_id
    while behind_distance < behind_m and len(admitted) < max_edges:
        lookup = provider.get_incoming(behind_node, branch_limit)
        truncated |= lookup.truncated
        candidates = _backward_candidates(lookup.segments, admitted, behind_nodes)
        # More than one predecessor is a merge viewed backwards.  Picking one
        # would invent vehicle history, so stop before it.
        if len(candidates) != 1 or lookup.truncated:
            break
        edge = candidates[0]
        if behind_distance + edge.length_m > behind_m:
            break
        _admit(edge, admitted, behind_nodes)
        behind.append(edge.internal_segment_id)
        behind_distance += edge.length_m
        behind_node = edge.from_node_id

    if len(admitted) >= max_edges and (behind_distance < behind_m):
        truncated = True

    return RelevantPath(
        under=start.internal_segment_id,
        behind=tuple(behind),
        common_ahead=tuple(common),
        branches=tuple(branches),
        behind_distance_m=behind_distance,
        common_ahead_distance_m=common_distance,
        branch_confidence=0.0 if fork_detected else 1.0,
        truncated=truncated,
        terminal_reason=terminal_reason,
    )


def _follow_branch(
    provider: GraphSegmentProvider,
    first_edge: GraphSegment,
    *,
    admitted: set[str],
    base_visited_nodes: set[str],
    distance_limit: float,
    max_edges: int,
    branch_limit: int,
) -> tuple[list[str], float, str, bool]:
    if first_edge.length_m > distance_limit:
        return [], 0.0, "distance_limit", False

    path: list[str] = []
    distance = 0.0
    visited_nodes = set(base_visited_nodes)
    edge = first_edge
    truncated = False
    while True:
        if edge.internal_segment_id in admitted or edge.to_node_id in visited_nodes:
            return path, distance, "cycle", truncated
        if len(admitted) >= max_edges:
            return path, distance, "edge_limit", True
        if distance + edge.length_m > distance_limit:
            return path, distance, "distance_limit", truncated

        _admit(edge, admitted, visited_nodes)
        path.append(edge.internal_segment_id)
        distance += edge.length_m
        if distance >= distance_limit:
            return path, distance, "distance_limit", truncated

        lookup = provider.get_outgoing(edge.to_node_id, branch_limit)
        truncated |= lookup.truncated
        candidates = _forward_candidates(lookup.segments, admitted, visited_nodes)
        if not candidates:
            return path, distance, "dead_end", truncated
        if len(candidates) != 1 or lookup.truncated:
            return path, distance, "fork", truncated
        edge = candidates[0]


def _forward_candidates(
    segments: tuple[GraphSegment, ...],
    admitted: set[str],
    visited_nodes: set[str],
) -> tuple[GraphSegment, ...]:
    # Following from_node -> to_node respects oneway=-1 as well: the importer
    # already reverses that edge's endpoints.  Returning to a visited node is
    # an immediate U-turn or cycle, never the road ahead.
    return tuple(
        edge
        for edge in segments
        if edge.internal_segment_id not in admitted and edge.to_node_id not in visited_nodes
    )


def _backward_candidates(
    segments: tuple[GraphSegment, ...],
    admitted: set[str],
    visited_nodes: set[str],
) -> tuple[GraphSegment, ...]:
    return tuple(
        edge
        for edge in segments
        if edge.internal_segment_id not in admitted
        and edge.from_node_id not in visited_nodes
    )


def _admit(
    edge: GraphSegment,
    admitted: set[str],
    visited_nodes: set[str],
) -> None:
    admitted.add(edge.internal_segment_id)
    visited_nodes.add(edge.from_node_id)
    visited_nodes.add(edge.to_node_id)


def _to_segment(row) -> GraphSegment:
    length = row.length_m
    if isinstance(length, Decimal):
        length = float(length)
    return GraphSegment(
        internal_segment_id=row.internal_segment_id,
        from_node_id=row.from_node_id,
        to_node_id=row.to_node_id,
        length_m=max(0.0, float(length or 0.0)),
    )
