from ndwinfo.api.routers.traffic import _normalized_road_number


def test_normalized_road_number_matches_weggeg_format():
    assert _normalized_road_number("A1") == "001"
    assert _normalized_road_number("N 001") == "001"
    assert _normalized_road_number("A76a") == "076"
    assert _normalized_road_number("001") == "001"
    assert _normalized_road_number(None) is None
