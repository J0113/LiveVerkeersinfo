import pytest
from fastapi import HTTPException

from ndwinfo.api.routers.traffic import _normalize_carriageway
from ndwinfo.ingest.vild_direction import EffectiveRoadInput, compute_effective_road


def site(id, road=None, carriageway=None, vild_carriageway=None, vild_road_number=None,
          side=None, tmc_direction=None, coords=None):
    return EffectiveRoadInput(
        id=id,
        road=road,
        carriageway=carriageway,
        vild_carriageway=vild_carriageway,
        vild_road_number=vild_road_number,
        side=side,
        tmc_direction=tmc_direction,
        coords=coords,
    )


def test_explicit_road_and_carriageway_win_outright():
    resolved = compute_effective_road([site("a", road="A2", carriageway="R")])
    assert resolved["a"] == ("A2", "R", "explicit")


def test_missing_road_falls_back_to_vild_road_number():
    resolved = compute_effective_road([site("a", vild_road_number="A2", carriageway="R")])
    assert resolved["a"] == ("A2", "R", "vild_road_number")


def test_missing_carriageway_falls_back_to_vild_carriageway():
    resolved = compute_effective_road([site("a", road="A2", vild_carriageway="L")])
    assert resolved["a"] == ("A2", "L", "explicit")


def test_nothing_known_resolves_to_none():
    resolved = compute_effective_road([site("a")])
    assert resolved["a"] == (None, None, None)


def test_colocated_sibling_with_unambiguous_road_is_inherited():
    coords = (4.71, 52.51)
    resolved = compute_effective_road([
        site("known", road="A2", carriageway="R", coords=coords, side="right"),
        site("unknown", coords=coords, side="right"),
    ])
    assert resolved["unknown"] == ("A2", "R", "inherited")


def test_colocated_siblings_disagreeing_are_not_inherited():
    coords = (4.71, 52.51)
    resolved = compute_effective_road([
        site("a", road="A2", carriageway="R", coords=coords, side="right"),
        site("b", road="A4", carriageway="R", coords=coords, side="right"),
        site("unknown", coords=coords, side="right"),
    ])
    assert resolved["unknown"] == (None, None, None)


def test_inherit_requires_same_side_and_direction():
    coords = (4.71, 52.51)
    resolved = compute_effective_road([
        site(
            "known", road="A2", carriageway="R", coords=coords,
            side="right", tmc_direction="positive",
        ),
        site("unknown", coords=coords, side="left", tmc_direction="negative"),
    ])
    assert resolved["unknown"] == (None, None, None)


def test_missing_coords_never_inherits():
    resolved = compute_effective_road([
        site("known", road="A2", carriageway="R", coords=(4.71, 52.51)),
        site("no_geom", coords=None),
    ])
    assert resolved["no_geom"] == (None, None, None)


def test_normalize_carriageway_accepts_lowercase_and_whitespace():
    assert _normalize_carriageway(" r ") == "R"
    assert _normalize_carriageway("l") == "L"
    assert _normalize_carriageway(None) is None


def test_normalize_carriageway_rejects_invalid_value():
    with pytest.raises(HTTPException):
        _normalize_carriageway("X")
