"""Persistence-scope tests for the independent OSM Lanes rebuild command."""

from __future__ import annotations

from types import SimpleNamespace

from ndwinfo.models import OsmLaneCenterline, OsmLaneConnection
from ndwinfo.refresh_osm_lane_lines import rebuild


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, existing_rows):
        self.existing_rows = existing_rows
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _Result(self.existing_rows)
        return _Result()

    def flush(self):
        pass


def _road(osm_id, tags, nodes, coordinates):
    return SimpleNamespace(
        OsmRoad=SimpleNamespace(
            osm_id=osm_id,
            highway=tags["highway"],
            raw=tags,
            node_refs=nodes,
        ),
        geom_wkt=(
            "LINESTRING ("
            + ", ".join(f"{lon} {lat}" for lon, lat in coordinates)
            + ")"
        ),
    )


def test_bbox_rebuild_rewrites_stale_lanes_and_cross_boundary_connectors(
    monkeypatch,
):
    selected = _road(
        1,
        {"highway": "primary", "oneway": "yes", "lanes": "2"},
        [10, 11],
        [(4.7, 52.5), (4.701, 52.5)],
    )
    context = _road(
        2,
        {"highway": "primary", "oneway": "yes", "lanes": "1"},
        [11, 12],
        [(4.701, 52.5), (4.702, 52.5)],
    )
    monkeypatch.setattr(
        "ndwinfo.refresh_osm_lane_lines._load_selected_roads",
        lambda *_args, **_kwargs: [selected],
    )
    monkeypatch.setattr(
        "ndwinfo.refresh_osm_lane_lines._load_context_roads",
        lambda *_args, **_kwargs: [selected, context],
    )
    monkeypatch.setattr(
        "ndwinfo.refresh_osm_lane_lines.load_connection_overrides",
        lambda _path: [
            {
                "action": "connect",
                "from": "ll:99:1:2:fwd:1@fwd",
                "to": "ll:100:2:3:fwd:1@fwd",
            }
        ],
    )

    captured_overrides = []
    all_connections = [
        {"id": "touching", "from_road_id": 1, "to_road_id": 2},
        {"id": "context-only", "from_road_id": 2, "to_road_id": 2},
    ]

    def fake_build_network(lane_rows, _contexts, *, overrides):
        captured_overrides.extend(overrides)
        return lane_rows, all_connections, [], {}, set()

    monkeypatch.setattr(
        "ndwinfo.refresh_osm_lane_lines.build_lane_network",
        fake_build_network,
    )
    inserted = {}

    def fake_insert(_session, model, rows):
        inserted[model] = list(rows)

    monkeypatch.setattr(
        "ndwinfo.refresh_osm_lane_lines._insert_batches",
        fake_insert,
    )

    session = _Session(
        [
            (1, "ll:1:10:11:fwd:1"),
            (1, "ll:1:10:11:fwd:2"),
            (1, "ll:1:10:11:fwd:3"),
            (2, "ll:2:11:12:fwd:1"),
        ]
    )
    counters, diagnostics = rebuild(
        session,
        bbox=(4.699, 52.499, 4.7015, 52.501),
    )

    assert diagnostics == []
    assert captured_overrides == []
    assert {
        row["id"] for row in inserted[OsmLaneCenterline]
    } == {
        "ll:1:10:11:fwd:1",
        "ll:1:10:11:fwd:2",
    }
    assert inserted[OsmLaneConnection] == [all_connections[0]]
    assert counters["lane_lines"] == 2
    assert counters["connections"] == 1

    delete_sql = [
        str(statement.compile(compile_kwargs={"literal_binds": True}))
        for statement in session.statements[1:]
    ]
    assert all("IN (1)" in statement for statement in delete_sql)
