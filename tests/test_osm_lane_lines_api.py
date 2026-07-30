"""API contract tests for the independent OSM Lanes endpoint."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from ndwinfo.api.deps import BBox
from ndwinfo.api.routers.osm import _lane_tag_value, get_osm_lane_lines
from ndwinfo.config import settings
from ndwinfo.refresh_osm_lane_lines import rebuild


def _lane(lane_id: str, lane_nr: int):
    return SimpleNamespace(
        id=lane_id,
        road_id=42,
        segment_id="42:10:11",
        lane_nr=lane_nr,
        lane_count=2,
        direction="fwd",
        offset_m=1.75 if lane_nr == 1 else -1.75,
        count_source="lanes",
        oneway_source="tag",
    )


def test_lane_lines_api_keeps_separate_caps_and_parent_properties(monkeypatch):
    monkeypatch.setattr(settings, "osm_lane_line_max_features", 1)
    monkeypatch.setattr(settings, "osm_lane_connection_max_features", 1)
    lane_rows = [
        SimpleNamespace(
            OsmLaneCenterline=_lane("ll:42:10:11:fwd:1", 1),
            osm_tags={
                "highway": "motorway",
                "name": "Testweg",
                "ref": "A1",
                "turn:lanes": "none|merge_to_left",
                "placement": "right_of:1",
                "destination:lanes": "Amsterdam|Haarlem",
                "destination:ref:lanes": "A1|A9",
                "change:lanes": "yes|not_right",
            },
            geom_json='{"type":"LineString","coordinates":[[4.7,52.5],[4.71,52.51]]}',
        ),
        SimpleNamespace(
            OsmLaneCenterline=_lane("ll:42:10:11:fwd:2", 2),
            osm_tags={"highway": "motorway", "name": "Testweg", "ref": "A1"},
            geom_json='{"type":"LineString","coordinates":[[4.7,52.5],[4.71,52.51]]}',
        ),
    ]
    connection = SimpleNamespace(
        id="ll:42:10:11:fwd:1@fwd>ll:43:11:12:fwd:1@fwd",
        from_lane_id="ll:42:10:11:fwd:1",
        from_direction="fwd",
        to_lane_id="ll:43:11:12:fwd:1",
        to_direction="fwd",
        connection_type="continuation",
        confidence="exact",
        raw={"from_trim_m": 7.0, "to_trim_m": 7.0},
    )
    connection_rows = [
        SimpleNamespace(
            OsmLaneConnection=connection,
            geom_json='{"type":"LineString","coordinates":[[4.71,52.51],[4.72,52.52]]}',
        )
    ]

    class Db:
        calls = 0

        def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(all=lambda: lane_rows)
            if self.calls == 2:
                return SimpleNamespace(all=lambda: connection_rows)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [connection])
            )

    response = get_osm_lane_lines(BBox(4.6, 52.4, 4.8, 52.6), Db())
    body = json.loads(response.body)

    assert [feature["properties"]["kind"] for feature in body["features"]] == [
        "lane",
        "connection",
    ]
    assert body["features"][0]["properties"]["ref"] == "A1"
    assert body["features"][0]["properties"]["id"].startswith("ll:")
    assert body["features"][0]["properties"]["turn:lanes"] == "none|merge_to_left"
    assert body["features"][0]["properties"]["turn_lane"] == "none"
    assert body["features"][0]["properties"]["placement"] == "right_of:1"
    assert body["features"][0]["properties"]["destination_lane"] == "Amsterdam"
    assert body["features"][0]["properties"]["destination_ref_lane"] == "A1"
    assert body["features"][0]["properties"]["change_lane"] == "yes"
    assert body["features"][0]["geometry"]["coordinates"][-1] != [4.71, 52.51]
    assert body["metadata"] == {
        "truncated": True,
        "truncated_by_kind": {"lanes": True, "connections": False},
    }


def test_lane_popup_tag_selection_uses_strict_backward_fallback():
    bidirectional = {"turn:lanes": "left|right"}
    reverse_oneway = {"oneway": "-1", "turn:lanes": "none|merge_to_left"}

    assert _lane_tag_value(
        bidirectional,
        "turn:lanes",
        "bwd",
        1,
        2,
    ) is None
    assert _lane_tag_value(
        reverse_oneway,
        "turn:lanes",
        "bwd",
        2,
        2,
    ) == "merge_to_left"


def test_independent_lane_endpoint_and_rebuilder_do_not_use_lane_detail():
    assert "OsmRoadLane" not in inspect.getsource(get_osm_lane_lines)
    assert "OsmRoadLane" not in inspect.getsource(rebuild)
