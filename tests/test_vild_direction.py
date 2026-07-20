from shapely.geometry import LineString, MultiLineString, Point

from ndwinfo.ingest.vild_direction import derive_direction
from ndwinfo.parsers.datex_v2 import _parse_site_location


def derive(**overrides):
    values = {
        "line": LineString([(0, 0), (0, 100)]),
        "tmc_point": Point(0, 20),
        "neighbour_point": Point(0, 80),
        "neighbour_is_positive": True,
        "sensor_point": Point(2, 50),
        "tmc_direction": "positive",
        "hecto_dir": 1,
        "explicit_carriageway": None,
    }
    values.update(overrides)
    return derive_direction(**values)


def test_positive_and_negative_tmc_direction_orient_the_local_tangent():
    assert derive().bearing == 0.0
    assert derive(tmc_direction="negative").bearing == 180.0


def test_raw_line_order_is_not_assumed_to_be_positive():
    result = derive(
        line=LineString([(0, 100), (0, 0)]),
        tmc_point=Point(0, 20),
        neighbour_point=Point(0, 80),
    )
    assert result.bearing == 0.0


def test_negative_neighbour_establishes_positive_orientation():
    result = derive(
        tmc_point=Point(0, 80),
        neighbour_point=Point(0, 20),
        neighbour_is_positive=False,
    )
    assert result.bearing == 0.0


def test_two_tmc_points_are_sufficient_when_vild_line_geometry_is_missing():
    result = derive(line=LineString([(0, 20), (0, 80)]))
    assert result.bearing == 0.0


def test_endpoint_projection_uses_a_one_sided_local_tangent():
    assert derive(sensor_point=Point(2, 0)).bearing == 0.0
    assert derive(sensor_point=Point(2, 100)).bearing == 0.0


def test_hecto_dir_derives_rl_without_overriding_tmc_bearing():
    result = derive(hecto_dir=-1, explicit_carriageway="R")
    assert result.bearing == 0.0
    assert result.derived_carriageway == "L"
    assert result.conflict is True


def test_hecto_dir_zero_leaves_rl_unresolved():
    result = derive(hecto_dir=0, explicit_carriageway="R")
    assert result.derived_carriageway is None
    assert result.conflict is None


def test_ambiguous_multiline_component_is_not_guessed():
    result = derive(
        line=MultiLineString([
            [(0, 0), (0, 100)],
            [(0.2, 0), (0.2, 100)],
        ]),
        sensor_point=Point(0.1, 50),
    )
    assert result.bearing is None


def test_geo_parser_keeps_only_explicit_hrl_hrr_carriageway():
    explicit = _parse_site_location("GEO0B_R_X", "009hrl057760", "positive")
    explicit_right = _parse_site_location("GEO0B_R_X", "009hrr057760", "negative")
    connector = _parse_site_location("GEO0B_R_X", "009vwb058082", "positive")
    assert explicit["carriageway"] == "L"
    assert explicit_right["carriageway"] == "R"
    assert explicit["carriageway_source"] == "measurement_site_name"
    assert connector["carriageway"] is None
    assert connector["carriageway_source"] is None


def test_rws01_no_longer_maps_tmc_direction_directly_to_rl():
    result = _parse_site_location("RWS01_X", "0091vwb0572ra", "positive")
    assert result["carriageway"] is None
    assert result["carriageway_source"] is None


def test_rws08_and_provincial_explicit_rl_codes_remain_authoritative():
    rws08 = _parse_site_location("RWS08_A30_HRL_021.8_1", None, "positive")
    provincial = _parse_site_location("PZH01_X", "N457 hmp 4.75 Li", "positive")

    assert (rws08["carriageway"], rws08["carriageway_source"]) == (
        "L",
        "measurement_site_id",
    )
    assert (provincial["carriageway"], provincial["carriageway_source"]) == (
        "L",
        "measurement_site_name",
    )
