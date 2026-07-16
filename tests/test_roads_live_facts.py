from datetime import datetime, timezone
from types import SimpleNamespace

from ndwinfo.api.routers.roads import load_live_segment_facts


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, *results):
        self._results = iter(results)

    def execute(self, _query):
        return _Result(next(self._results))


def test_live_facts_keep_msi_lane_but_never_assign_lane_to_drip():
    now = datetime.now(timezone.utc)
    matrix = SimpleNamespace(
        internal_segment_id="segment-1",
        source_id="msi-1",
        confidence=0.91,
        road="A4",
        carriageway="R",
        source_lane=1,
        km=12.3,
        segment_lane_count=1,
        aspect_type="speedlimit",
        value="70",
        flashing=False,
        red_ring=True,
        ts_state=now,
        ingested_at=now,
        offset_m=80.0,
    )
    drip = SimpleNamespace(
        internal_segment_id="segment-1",
        source_id="controller-1:1",
        confidence=0.88,
        description="DRIP A4",
        display_text="FILE 4 KM",
        vms_type="text",
        message={"pages": 1},
        ingested_at=now,
        offset_m=140.0,
    )

    facts = load_live_segment_facts(
        _Db([matrix], [drip]), "graph-v1", ["segment-1"]
    )["segment-1"]

    assert facts["matrix"][0]["lane"] == 1
    assert facts["matrix"][0]["source_lane"] == 1
    assert facts["drips"][0]["source_id"] == "controller-1:1"
    assert "lane" not in facts["drips"][0]


def test_blank_matrix_state_is_not_exposed_as_actionable_fact():
    now = datetime.now(timezone.utc)
    blank = SimpleNamespace(
        internal_segment_id="segment-1",
        source_id="msi-blank",
        confidence=0.99,
        road="A4",
        carriageway="R",
        source_lane=1,
        km=12.3,
        segment_lane_count=1,
        aspect_type="blank",
        value=None,
        flashing=False,
        red_ring=False,
        ts_state=now,
        ingested_at=now,
        offset_m=20.0,
    )

    facts = load_live_segment_facts(
        _Db([blank], []), "graph-v1", ["segment-1"]
    )["segment-1"]

    assert facts == {"matrix": [], "drips": []}


def test_incomplete_msi_numbering_keeps_carriageway_but_withholds_lane():
    now = datetime.now(timezone.utc)
    incomplete = SimpleNamespace(
        internal_segment_id="segment-1",
        source_id="msi-2",
        confidence=0.9,
        road="A4",
        carriageway="R",
        source_lane=2,
        km=12.3,
        segment_lane_count=3,
        aspect_type="speedlimit",
        value="70",
        flashing=False,
        red_ring=False,
        ts_state=now,
        ingested_at=now,
        offset_m=80.0,
    )

    fact = load_live_segment_facts(
        _Db([incomplete], []), "graph-v1", ["segment-1"]
    )["segment-1"]["matrix"][0]

    assert fact["lane_scope_status"] == "source_only"
    assert fact["source_lane"] == 2
    assert "lane" not in fact
