import pytest
from sqlalchemy.dialects import postgresql

from ndwinfo.osm.graph_query import (
    GraphSegment,
    SegmentLookup,
    SegmentNotFoundError,
    SqlGraphSegmentProvider,
    find_relevant_path,
)


def edge(segment_id: str, from_node: str, to_node: str, length_m: float = 100.0):
    return GraphSegment(segment_id, from_node, to_node, length_m)


class MemoryProvider:
    def __init__(self, segments):
        self.segments = {segment.internal_segment_id: segment for segment in segments}

    def get_segment(self, internal_segment_id):
        return self.segments.get(internal_segment_id)

    def get_outgoing(self, from_node_id, limit):
        return self._lookup("from_node_id", from_node_id, limit)

    def get_incoming(self, to_node_id, limit):
        return self._lookup("to_node_id", to_node_id, limit)

    def _lookup(self, field, node_id, limit):
        found = sorted(
            (
                segment
                for segment in self.segments.values()
                if getattr(segment, field) == node_id
            ),
            key=lambda segment: segment.internal_segment_id,
        )
        return SegmentLookup(tuple(found[:limit]), len(found) > limit)


def test_directional_path_stops_common_scope_at_fork_and_keeps_unique_behind():
    provider = MemoryProvider(
        [
            edge("behind", "P", "U", 80),
            edge("under", "U", "A", 100),
            edge("reverse-under", "A", "U", 100),
            edge("common", "A", "B", 120),
            edge("left", "B", "C", 90),
            edge("left-next", "C", "E", 110),
            edge("right", "B", "D", 95),
            edge("right-next", "D", "F", 105),
            # Geometrically this could cross the route, but it has no shared
            # endpoint and is therefore unreachable by topology.
            edge("grade-separated-crossing", "X", "Y", 10),
        ]
    )

    result = find_relevant_path(
        provider,
        "under",
        ahead_m=500,
        behind_m=100,
        max_edges=20,
        branch_limit=8,
    )

    assert result.under == "under"
    assert result.behind == ("behind",)
    assert result.common_ahead == ("common",)
    assert [branch.segment_ids for branch in result.branches] == [
        ("left", "left-next"),
        ("right", "right-next"),
    ]
    assert result.branch_confidence == 0.0
    assert result.terminal_reason == "fork"
    assert "reverse-under" not in result.all_segment_ids
    assert "grade-separated-crossing" not in result.all_segment_ids


def test_unambiguous_path_is_bounded_by_distance_and_reports_limits():
    provider = MemoryProvider(
        [
            edge("under", "A", "B", 100),
            edge("next-1", "B", "C", 100),
            edge("next-2", "C", "D", 100),
        ]
    )
    result = find_relevant_path(
        provider,
        "under",
        ahead_m=150,
        behind_m=0,
        max_edges=10,
        branch_limit=4,
    )

    assert result.common_ahead == ("next-1",)
    assert result.branch_confidence == 1.0
    assert result.terminal_reason == "distance_limit"
    assert result.truncated is False
    assert result.as_dict()["limit_reached"] is True
    assert result.as_dict()["visited_segment_count"] == 2


def test_path_contract_serializes_roles_branches_and_budget_metadata():
    result = find_relevant_path(
        MemoryProvider(
            [
                edge("under", "A", "B", 50),
                edge("left", "B", "L", 75),
                edge("right", "B", "R", 80),
            ]
        ),
        "under",
        ahead_m=200,
        behind_m=0,
        max_edges=10,
        branch_limit=4,
    )

    payload = result.as_dict()
    assert payload["under"] == "under"
    assert payload["behind"] == []
    assert payload["common_ahead"] == []
    assert payload["branches"] == [
        {
            "segment_ids": ["left"],
            "distance_m": 75.0,
            "terminal_reason": "dead_end",
        },
        {
            "segment_ids": ["right"],
            "distance_m": 80.0,
            "terminal_reason": "dead_end",
        },
    ]
    assert payload["branch_confidence"] == 0.0
    assert payload["visited_segment_count"] == 3
    assert payload["limit_reached"] is False
    assert payload["truncated"] is False
    assert payload["terminal_reason"] == "fork"


def test_edge_budget_caps_total_returned_work_and_marks_truncated():
    provider = MemoryProvider(
        [
            edge("under", "A", "B", 50),
            edge("next-1", "B", "C", 50),
            edge("next-2", "C", "D", 50),
            edge("next-3", "D", "E", 50),
        ]
    )
    result = find_relevant_path(
        provider,
        "under",
        ahead_m=1000,
        behind_m=0,
        max_edges=2,
        branch_limit=4,
    )

    assert result.all_segment_ids == ("under", "next-1")
    assert result.truncated is True
    assert result.terminal_reason == "edge_limit"


def test_cycle_and_reverse_edge_are_not_followed():
    provider = MemoryProvider(
        [
            edge("under", "A", "B"),
            edge("next", "B", "C"),
            edge("cycle", "C", "A"),
        ]
    )
    result = find_relevant_path(
        provider,
        "under",
        ahead_m=1000,
        behind_m=0,
        max_edges=20,
        branch_limit=4,
    )
    assert result.common_ahead == ("next",)
    assert "cycle" not in result.all_segment_ids


def test_unknown_segment_fails_closed():
    with pytest.raises(SegmentNotFoundError):
        find_relevant_path(
            MemoryProvider([]),
            "missing",
            ahead_m=100,
            behind_m=0,
            max_edges=2,
            branch_limit=2,
        )


class _EmptyResult:
    def all(self):
        return []


class _CaptureDb:
    def __init__(self):
        self.query = None
        self.execute_count = 0

    def execute(self, query):
        self.query = query
        self.execute_count += 1
        return _EmptyResult()


def test_sql_provider_uses_graph_and_directed_endpoint_in_bounded_query():
    db = _CaptureDb()
    provider = SqlGraphSegmentProvider(db, import_run_id=7, graph_version="graph-v1")
    result = provider.get_outgoing("node-12", limit=8)

    sql = str(
        db.query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert result == SegmentLookup(())
    assert "osm_road_segment.import_run_id = 7" in sql
    assert "osm_road_segment.graph_version = 'graph-v1'" in sql
    assert "osm_road_segment.from_node_id = 'node-12'" in sql
    assert "osm_road_segment.to_node_id = 'node-12'" not in sql
    assert "LIMIT 9" in sql
    assert provider.lookup_count == 1
    assert provider.candidate_rows_loaded == 0

    # A converging branch can revisit an endpoint; it must not cause another
    # database round-trip within the same request.
    assert provider.get_outgoing("node-12", limit=8) == SegmentLookup(())
    assert db.execute_count == 1


def test_branch_provider_overflow_marks_result_truncated_and_unresolved():
    segments = [edge("under", "A", "B")]
    segments.extend(edge(f"branch-{index}", "B", f"N{index}") for index in range(4))
    result = find_relevant_path(
        MemoryProvider(segments),
        "under",
        ahead_m=500,
        behind_m=0,
        max_edges=20,
        branch_limit=2,
    )

    assert result.truncated is True
    assert result.branch_confidence == 0.0
    assert len(result.branches) == 2


def test_overflow_with_only_backtrack_visible_still_fails_closed():
    result = find_relevant_path(
        MemoryProvider(
            [
                edge("under", "A", "B"),
                edge("a-backtrack", "B", "A"),
                edge("z-valid-but-beyond-query-cap", "B", "C"),
            ]
        ),
        "under",
        ahead_m=500,
        behind_m=0,
        max_edges=20,
        branch_limit=1,
    )

    assert result.common_ahead == ()
    assert result.branches == ()
    assert result.branch_confidence == 0.0
    assert result.truncated is True
    assert result.terminal_reason == "fork"
