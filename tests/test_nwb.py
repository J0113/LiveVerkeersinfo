"""Unit tests for NWB Wegvakken GeoPackage parsing and matching helpers."""

from __future__ import annotations

import math

import pandas as pd
from shapely.geometry import LineString

from ndwinfo.nwb import TrafficMatchObservation, matching_keys
from ndwinfo.parsers.nwb_gpkg import _optional_float, _optional_int, _optional_str, _transform_row

LINE = LineString([(4.88, 52.36), (4.89, 52.37)])


def _props(**overrides):
    props = {
        "WVK_ID": 123456789,
        "JTE_ID_BEG": 10,
        "JTE_ID_END": 11,
        "WEGBEHSRT": "R",
        "WEGBEHNAAM": "Rijkswaterstaat",
        "STT_NAAM": "Ringweg",
        "RIJRICHTNG": "H",
        "ADMRICHTNG": "O",
        "RPE_CODE": "R",
        "POS_TV_WOL": "R",
        "BST_CODE": "HR",
        "FRC": "1",
        "FOW": 3,
        "OPENLR": "encoded-openlr",
        "BEGINKM": 3.2,
        "EINDKM": 3.5,
        "LENGTE_WVK": 301,
        "BEGDAT_WRK": pd.Timestamp("2026-07-01"),
        "STATUS": "Opengesteld",
        "WEGNR_HMP": "A10",
        "WEGNR_FRML": None,
        "ROUTELTR": None,
        "ROUTENR": None,
    }
    props.update(overrides)
    return props


def test_transform_row_maps_gpkg_fields_and_classifies_motorway():
    row = _transform_row(_props(), LINE)
    assert row is not None
    assert row["wvk_id"] == 123456789
    assert row["begin_junction_id"] == 10
    assert row["end_junction_id"] == 11
    assert row["road_manager_type"] == "R"
    assert row["carriageway_position"] == "R"
    assert row["carriageway_type"] == "HR"
    assert row["road_class"] == "motorway"  # frc <= 2
    assert row["road_number"] == "A10"
    assert row["valid_from"] == pd.Timestamp("2026-07-01").date()
    assert row["status"] == "Opengesteld"
    assert row["geom"] == LINE.wkt


def test_transform_row_classifies_primary_by_frc_and_by_manager_type():
    assert _transform_row(_props(FRC="4", WEGBEHSRT="G"), LINE)["road_class"] == "primary"
    assert _transform_row(_props(FRC="9", WEGBEHSRT="P"), LINE)["road_class"] == "primary"
    assert _transform_row(_props(FRC="9", WEGBEHSRT="G"), LINE)["road_class"] == "local"


def test_transform_row_road_number_falls_back_to_route_letter_and_number():
    row = _transform_row(
        _props(WEGNR_HMP=None, WEGNR_FRML=None, ROUTELTR="A", ROUTENR=10), LINE
    )
    assert row["road_number"] == "A10"

    row = _transform_row(
        _props(WEGNR_HMP=None, WEGNR_FRML=None, ROUTELTR=None, ROUTENR=None), LINE
    )
    assert row["road_number"] is None


def test_transform_row_rejects_missing_id_or_degenerate_geometry():
    assert _transform_row(_props(WVK_ID=None), LINE) is None
    assert _transform_row(_props(), LineString()) is None  # empty geometry


def test_optional_helpers_treat_pandas_na_as_none():
    assert _optional_str(pd.NA) is None
    assert _optional_str(float("nan")) is None
    assert _optional_str("  A5  ") == "A5"
    assert _optional_int(pd.NA) is None
    assert _optional_int("7") == 7
    assert _optional_float(pd.NaT) is None
    assert _optional_float(math.inf) is None
    assert _optional_float("3.5") == 3.5


def test_matching_keys_extracts_identifiers_present_in_router_properties():
    feature = {
        "properties": {
            "segment_id": "123456789",
            "nwb_road_section_id": 123456789,
            "openlr": "encoded-openlr",
            "road_number": "A10",
            "direction": "H",
            "carriageway_position": "R",
            "road_class": "motorway",  # not part of matching_keys' allow-list
        }
    }
    assert matching_keys(feature) == {
        "segment_id": "123456789",
        "nwb_road_section_id": 123456789,
        "openlr": "encoded-openlr",
        "road_number": "A10",
        "direction": "H",
        "carriageway_position": "R",
    }


def test_matching_keys_handles_missing_properties():
    assert matching_keys({}) == {}
    assert matching_keys({"properties": None}) == {}


def test_traffic_match_observation_defaults_to_none():
    obs = TrafficMatchObservation()
    assert obs.nwb_road_section_id is None
    assert obs.openlr is None
    assert obs.road_number is None
    assert obs.carriageway is None
    assert obs.bearing is None
