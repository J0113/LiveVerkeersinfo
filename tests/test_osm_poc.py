import pytest
from fastapi import HTTPException

from ndwinfo.api.routers import osm_poc as osm_poc_router
from ndwinfo.osm_poc import link_measurements_to_roads, parse_overpass_roads


def way(way_id, coords, **tags):
    return {
        "type": "way",
        "id": way_id,
        "version": 3,
        "timestamp": "2026-07-15T10:00:00Z",
        "tags": tags,
        "geometry": [{"lon": lon, "lat": lat} for lon, lat in coords],
    }


def measurement(lon, lat, *, road="A1", bearing=90, carriageway=None, speed=0):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "site_id": "site-1",
            "road": road,
            "carriageway": carriageway,
            "openlr_bearing": bearing,
            "num_lanes": 2,
            "measured_at": "2026-07-15T10:00:00+00:00",
            "lanes": [{"lane": 1, "speed_kmh": speed}],
        },
    }


def test_two_way_osm_way_becomes_two_directed_edges():
    roads = parse_overpass_roads(
        {"elements": [way(42, [(5.0, 52.0), (5.01, 52.0)], highway="primary")]}
    )

    assert [f["properties"]["travel_direction"] for f in roads["features"]] == [
        "forward",
        "backward",
    ]
    assert roads["features"][1]["geometry"]["coordinates"][0] == [5.01, 52.0]


def test_oneway_minus_one_reverses_way_geometry():
    roads = parse_overpass_roads(
        {
            "elements": [
                way(
                    43,
                    [(5.0, 52.0), (5.01, 52.0)],
                    highway="motorway_link",
                    oneway="-1",
                )
            ]
        }
    )

    assert len(roads["features"]) == 1
    assert roads["features"][0]["geometry"]["coordinates"][0] == [5.01, 52.0]


def test_measurement_links_to_same_ref_and_direction_and_preserves_zero_speed():
    roads = parse_overpass_roads(
        {
            "elements": [
                way(
                    1,
                    [(5.0, 52.0), (5.01, 52.0)],
                    highway="motorway",
                    oneway="yes",
                    ref="A1",
                    lanes="2",
                ),
                way(
                    2,
                    [(5.0, 52.0003), (5.01, 52.0003)],
                    highway="motorway",
                    oneway="-1",
                    ref="A1",
                    lanes="2",
                ),
            ]
        }
    )
    points = {
        "type": "FeatureCollection",
        "features": [measurement(5.005, 52.00002, speed=0)],
    }

    summary = link_measurements_to_roads(roads, points)

    assert summary["matched_count"] == 1
    assert points["features"][0]["properties"]["osm_way_id"] == 1
    assert roads["features"][0]["properties"]["speed_kmh"] == 0


def test_explicit_road_reference_conflict_is_rejected():
    roads = parse_overpass_roads(
        {
            "elements": [
                way(
                    3,
                    [(5.0, 52.0), (5.01, 52.0)],
                    highway="motorway",
                    oneway="yes",
                    ref="A2",
                )
            ]
        }
    )
    points = {
        "type": "FeatureCollection",
        "features": [measurement(5.005, 52.0, road="A1")],
    }

    summary = link_measurements_to_roads(roads, points)

    assert summary["unmatched_count"] == 1
    assert points["features"][0]["properties"]["osm_match_status"] == "unmatched"


def test_missing_heading_keeps_opposite_directions_ambiguous():
    roads = parse_overpass_roads(
        {
            "elements": [
                way(
                    4,
                    [(5.0, 52.0), (5.01, 52.0)],
                    highway="primary",
                    ref="N1",
                    lanes="2",
                )
            ]
        }
    )
    points = {
        "type": "FeatureCollection",
        "features": [measurement(5.005, 52.0, road="N1", bearing=None)],
    }

    summary = link_measurements_to_roads(roads, points)

    assert summary["ambiguous_count"] == 1
    assert points["features"][0]["properties"]["osm_match_status"] == "ambiguous"


def test_overpass_diagnostics_reject_parallel_requests_without_leaking_key_lock():
    key = "major:test-concurrency"
    osm_poc_router._cache.pop(key, None)
    osm_poc_router._key_locks.pop(key, None)
    assert osm_poc_router._overpass_slot.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            osm_poc_router._get_cached_roads(
                key,
                (4.60, 52.30, 4.61, 52.31),
                "major",
            )
        assert error.value.status_code == 429
        assert key not in osm_poc_router._key_locks
    finally:
        osm_poc_router._overpass_slot.release()
