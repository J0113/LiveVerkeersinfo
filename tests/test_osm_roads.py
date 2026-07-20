"""Unit tests for OSM driving-road parsing, ingestion, and API zoom-tiering."""

from __future__ import annotations

from datetime import timezone
from types import SimpleNamespace

import pytest

from ndwinfo.api.routers.osm import _highway_types_for_zoom
from ndwinfo.ingest import osm_roads
from ndwinfo.parsers.osm_pbf import _way_row

UTC = timezone.utc
WKT = "LINESTRING(4.9 52.3,4.91 52.31)"


# ─── parser: _way_row ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "highway",
    [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
    ],
)
def test_way_row_accepts_driving_road_classes(highway):
    row = _way_row(1, {"highway": highway}, WKT)
    assert row is not None
    assert row["highway"] == highway
    assert row["osm_id"] == 1


@pytest.mark.parametrize("highway", ["residential", "footway", "cycleway", "service", None])
def test_way_row_rejects_non_driving_road_classes(highway):
    tags = {"highway": highway} if highway else {}
    assert _way_row(1, tags, WKT) is None


def test_way_row_rejects_missing_geometry():
    assert _way_row(1, {"highway": "motorway"}, None) is None
    assert _way_row(1, {"highway": "motorway"}, "") is None


def test_way_row_retains_full_tag_dict_verbatim():
    tags = {
        "highway": "primary",
        "name": "Rijksstraatweg",
        "ref": "N99",
        "maxspeed": "80",
        "surface": "asphalt",
        "lanes": "1",
        "oneway": "yes",
        "operator": "Rijkswaterstaat",
        "zone:traffic": "NL:rural",
    }
    row = _way_row(6569948, tags, WKT)
    assert row["name"] == "Rijksstraatweg"
    assert row["ref"] == "N99"
    # raw must carry every input tag, not just the extracted name/ref columns.
    assert row["raw"] == tags
    assert row["raw"]["maxspeed"] == "80"
    assert row["raw"]["operator"] == "Rijkswaterstaat"


# ─── ingester: extract-scoped upsert/prune ──────────────────────────────────


class FakeSession:
    def __init__(self):
        self.executions = []
        self.flushes = 0

    def execute(self, statement, parameters=None):
        captured = list(parameters) if isinstance(parameters, list) else parameters
        self.executions.append((statement, captured))
        return SimpleNamespace(rowcount=0)

    def flush(self):
        self.flushes += 1


def _rows(n, start=1):
    return [
        {
            "osm_id": start + i,
            "highway": "motorway",
            "name": None,
            "ref": None,
            "geom": WKT,
            "raw": {"highway": "motorway"},
        }
        for i in range(n)
    ]


def test_ingest_upserts_road_and_extract_membership_per_batch(monkeypatch):
    upsert_calls = []

    def fake_bulk_upsert(_session, model, rows, conflict_cols):
        upsert_calls.append((model.__name__, len(rows), conflict_cols))
        return len(rows)

    monkeypatch.setattr(osm_roads, "parse_roads", lambda _path: iter(_rows(3)))
    monkeypatch.setattr(osm_roads, "bulk_upsert", fake_bulk_upsert)
    monkeypatch.setattr(osm_roads, "wkt_geom", lambda v: v)
    monkeypatch.setattr(osm_roads, "json_safe", lambda v: v)

    ingester = osm_roads.OsmRoadIngester(feed_name="osm_noord_holland", extract_key="noord-holland")
    session = FakeSession()
    total = ingester._ingest(SimpleNamespace(path="ignored"), session)

    assert total == 3
    assert ("OsmRoad", 3, ["osm_id"]) in upsert_calls
    assert ("OsmRoadExtract", 3, ["extract_key", "osm_id"]) in upsert_calls
    # Three deletes: this batch's stale osm_road_lane rows (per-batch, inside
    # _flush), then the two end-of-run prunes (stale extract memberships,
    # then orphaned roads).
    assert len(session.executions) == 3
    stmt_texts = [str(stmt) for stmt, _ in session.executions]
    assert any("osm_road_lane" in t for t in stmt_texts)
    assert any("osm_road_extract" in t for t in stmt_texts)
    # The orphaned-roads prune targets osm_road specifically, not just any
    # statement that happens to mention a table whose name contains it.
    assert any(t.strip().startswith("DELETE FROM osm_road ") for t in stmt_texts)


def test_ingest_prune_is_scoped_to_this_extract_key(monkeypatch):
    monkeypatch.setattr(osm_roads, "parse_roads", lambda _path: iter(_rows(1)))
    monkeypatch.setattr(osm_roads, "bulk_upsert", lambda *a, **k: 1)
    monkeypatch.setattr(osm_roads, "wkt_geom", lambda v: v)
    monkeypatch.setattr(osm_roads, "json_safe", lambda v: v)

    ingester = osm_roads.OsmRoadIngester(feed_name="osm_zeeland", extract_key="zeeland")
    session = FakeSession()
    ingester._ingest(SimpleNamespace(path="ignored"), session)

    stmt_texts = [str(stmt) for stmt, _ in session.executions]
    extract_prune_stmt = next(t for t in stmt_texts if "osm_road_extract" in t)
    assert "extract_key" in extract_prune_stmt


def test_ingest_raises_without_pruning_on_zero_rows(monkeypatch):
    monkeypatch.setattr(osm_roads, "parse_roads", lambda _path: iter([]))
    monkeypatch.setattr(osm_roads, "bulk_upsert", lambda *a, **k: 0)

    ingester = osm_roads.OsmRoadIngester(feed_name="osm_noord_holland", extract_key="noord-holland")
    session = FakeSession()

    with pytest.raises(RuntimeError):
        ingester._ingest(SimpleNamespace(path="ignored"), session)

    # No delete should have run — a bad parse must not erase the existing layer.
    assert session.executions == []


# ─── API: zoom-tiered highway filtering ─────────────────────────────────────


def test_highway_types_hidden_below_min_zoom():
    assert _highway_types_for_zoom(6) == ()
    assert _highway_types_for_zoom(0) == ()


def test_highway_types_national_overview_motorway_only():
    assert _highway_types_for_zoom(7) == ("motorway", "motorway_link")
    assert _highway_types_for_zoom(8.9) == ("motorway", "motorway_link")


def test_highway_types_regional_adds_trunk_and_primary():
    types = _highway_types_for_zoom(9)
    assert set(types) == {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
    }
    assert _highway_types_for_zoom(10.9) == types


def test_highway_types_detailed_returns_all_classes():
    assert _highway_types_for_zoom(11) is None
    assert _highway_types_for_zoom(18) is None
