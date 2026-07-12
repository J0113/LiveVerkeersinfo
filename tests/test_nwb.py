"""Focused tests for NWB request construction and GeoJSON normalization."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from ndwinfo.api.deps import BBox
from ndwinfo.api.routers import nwb as nwb_router
from ndwinfo.nwb import (
    NwbDetailProfile,
    TtlLruCache,
    build_query_params,
    detail_profile,
    fetch_road_segments,
    matching_keys,
    transform_feature,
)


def _raw_feature(feature_id: str = "stable-uuid", **overrides):
    props = {
        "wvk_id": 123456789,
        "jte_id_beg": 10,
        "jte_id_end": 11,
        "wegnummer": "A10",
        "stt_naam": "Ringweg",
        "wegbehsrt": "R",
        "wegbehnaam": "Rijkswaterstaat",
        "rijrichtng": "H",
        "admrichtng": "O",
        "rpe_code": "R",
        "pos_tv_wol": "R",
        "bst_code": "HR",
        "frc": "1",
        "fow": "3",
        "openlr": "encoded-openlr",
        "beginkm": 3.2,
        "eindkm": 3.5,
        "st_lengthshape": 301.5,
        "wvk_begdat": "2026-07-01T00:00:00Z",
    }
    props.update(overrides)
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {
            "type": "MultiLineString",
            "coordinates": [[[4.88, 52.36], [4.89, 52.37]]],
        },
        "properties": props,
    }


def test_detail_profiles_limit_server_side_scope():
    assert detail_profile(8.99) is None
    assert detail_profile(9).manager_types == ("R",)
    assert detail_profile(11).manager_types == ("R", "P")
    assert detail_profile(12).manager_types == (None,)
    assert detail_profile(12, configured_max=77).max_features == 77


def test_lane_matching_context_expands_around_viewport():
    viewport = BBox(4.68, 52.25, 4.69, 52.26)
    expanded = nwb_router._expanded_lane_bbox(viewport, 5.0)

    assert expanded.min_lon < viewport.min_lon
    assert expanded.min_lat < viewport.min_lat
    assert expanded.max_lon > viewport.max_lon
    assert expanded.max_lat > viewport.max_lat


def test_build_query_params_uses_crs84_bbox_and_manager_filter():
    params = build_query_params((4.8, 52.3, 4.9, 52.4), "R", limit=5000)
    assert params["bbox"] == "4.800000,52.300000,4.900000,52.400000"
    assert params["limit"] == 1000
    assert params["wegbehsrt"] == "R"
    assert params["crs"].endswith("/CRS84")
    assert params["bbox-crs"].endswith("/CRS84")

    with pytest.raises(ValueError):
        build_query_params((4.9, 52.3, 4.8, 52.4), None)


def test_transform_feature_preserves_matching_and_carriageway_metadata():
    result = transform_feature(_raw_feature())
    assert result is not None
    assert result["id"] == "stable-uuid"
    assert result["geometry"]["type"] == "MultiLineString"
    assert result["properties"]["nwb_road_section_id"] == 123456789
    assert result["properties"]["road_class"] == "motorway"
    assert result["properties"]["carriageway_position"] == "R"
    assert result["properties"]["carriageway_type"] == "HR"
    assert matching_keys(result) == {
        "segment_id": "stable-uuid",
        "nwb_road_section_id": 123456789,
        "openlr": "encoded-openlr",
        "road_number": "A10",
        "direction": "H",
        "carriageway_position": "R",
    }


def test_transform_feature_prefers_public_road_number():
    result = transform_feature(_raw_feature(wegnummer="005", wegnr_aw="RW5", wegnr_hmp="A5"))
    assert result["properties"]["road_number"] == "A5"


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {"type": "Point", "coordinates": [4.8, 52.3]},
        {"type": "MultiLineString", "coordinates": [[[float("nan"), 52.3]]]},
        {"type": "MultiLineString", "coordinates": [[[4.8, 95], [4.9, 52.4]]]},
    ],
)
def test_transform_feature_rejects_invalid_geometry(geometry):
    raw = _raw_feature()
    raw["geometry"] = geometry
    assert transform_feature(raw) is None


@pytest.mark.asyncio
async def test_fetch_road_segments_paginates_and_normalizes():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        query = parse_qs(request.url.query.decode())
        if "cursor" not in query:
            payload = {
                "type": "FeatureCollection",
                "features": [_raw_feature("one")],
                "links": [{"rel": "next", "href": "https://example.test/items?cursor=next"}],
            }
        else:
            payload = {
                "type": "FeatureCollection",
                "features": [
                    _raw_feature("two"),
                    {"id": "bad", "properties": {}, "geometry": None},
                ],
                "links": [],
            }
        return httpx.Response(
            200,
            content=json.dumps(payload),
            headers={"Content-Type": "application/geo+json"},
        )

    transport = httpx.MockTransport(handler)
    profile = NwbDetailProfile("test", 12, (None,), 10)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_road_segments(
            client, "https://example.test/items", (4.8, 52.3, 4.9, 52.4), profile
        )

    assert len(calls) == 2
    first_query = parse_qs(calls[0].url.query.decode())
    assert first_query["bbox"] == ["4.800000,52.300000,4.900000,52.400000"]
    assert result.invalid_features == 1
    assert result.truncated is False
    assert [f["id"] for f in result.feature_collection["features"]] == ["one", "two"]


@pytest.mark.asyncio
async def test_fetch_road_segments_marks_a_capped_viewport_as_truncated():
    def handler(_: httpx.Request) -> httpx.Response:
        payload = {
            "type": "FeatureCollection",
            "features": [_raw_feature("one"), _raw_feature("two")],
            "links": [],
        }
        return httpx.Response(200, json=payload)

    profile = NwbDetailProfile("capped", 12, (None,), 1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_road_segments(
            client, "https://example.test/items", (4.8, 52.3, 4.9, 52.4), profile
        )

    assert len(result.feature_collection["features"]) == 1
    assert result.truncated is True
    assert result.feature_collection["metadata"]["truncated"] is True


def test_ttl_lru_cache_evicts_oldest_entry():
    cache: TtlLruCache[str] = TtlLruCache(ttl_s=60, max_entries=2)
    cache.set("one", "1")
    cache.set("two", "2")
    assert cache.get("one") == "1"  # makes one most recently used
    cache.set("three", "3")
    assert cache.get("two") is None
    assert cache.get("one") == "1"
    assert cache.get("three") == "3"


@pytest.mark.asyncio
async def test_concurrent_road_cache_miss_is_coalesced(monkeypatch):
    calls = 0

    async def fake_fetch(client, url, bbox, profile):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return nwb_router.NwbFetchResult(
            feature_collection={"type": "FeatureCollection", "features": []},
            truncated=False,
            invalid_features=0,
        )

    monkeypatch.setattr(nwb_router, "fetch_road_segments", fake_fetch)
    profile = NwbDetailProfile("single-flight-test", 12, (None,), 10)
    bbox = (4.12345, 52.12345, 4.12445, 52.12445)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            nwb_router._cached_roads(client, bbox, profile),
            nwb_router._cached_roads(client, bbox, profile),
        )

    assert calls == 1
    assert sorted(status for _, status in results) == ["HIT", "MISS"]
