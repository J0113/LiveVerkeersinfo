from types import SimpleNamespace

from ndwinfo.api.routers.traffic import (
    _normalized_road_number,
    _pick_near_candidate,
    _pick_wide_candidate,
)


def test_normalized_road_number_matches_weggeg_format():
    assert _normalized_road_number("A1") == "001"
    assert _normalized_road_number("N 001") == "001"
    assert _normalized_road_number("A76a") == "076"
    assert _normalized_road_number("001") == "001"
    assert _normalized_road_number(None) is None


def candidate(source_id, distance, *, road=False, carriageway=False, lanes=False):
    return SimpleNamespace(
        source_id=source_id,
        distance_m=distance,
        road_match=road,
        carriageway_match=carriageway,
        lane_count_match=lanes,
    )


def test_near_match_prioritizes_road_then_carriageway_before_distance():
    candidates = [
        candidate("closest-wrong-road", 0.1, carriageway=True),
        candidate("same-road", 0.8, road=True),
        candidate("same-road-and-side", 2.4, road=True, carriageway=True),
    ]

    assert _pick_near_candidate(candidates).source_id == "same-road-and-side"


def test_near_match_uses_distance_after_metadata_ties():
    candidates = [
        candidate("farther", 2.0, road=True, carriageway=True),
        candidate("closer", 0.5, road=True, carriageway=True),
    ]

    assert _pick_near_candidate(candidates).source_id == "closer"


def test_wide_match_prioritizes_carriageway_then_lane_count():
    candidates = [
        candidate("closest-opposite", 3.0, road=True, lanes=True),
        candidate("same-side-wrong-lanes", 20.0, road=True, carriageway=True),
        candidate("same-side-and-lanes", 24.0, road=True, carriageway=True, lanes=True),
    ]

    assert _pick_wide_candidate(candidates).source_id == "same-side-and-lanes"
