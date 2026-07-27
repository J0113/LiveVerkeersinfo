"""Road-context resolution: which carriageway, and our hectometre on it."""

import json
import math
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import TextClause

from ndwinfo.api.deps import BBox
from ndwinfo.api.routers.traffic import (
    _along_track_m,
    _resolve_road_context,
    get_road_context,
    get_speed,
)

# Roughly A9 near Uitgeest; a degree of longitude here is ~67.8 km.
LON = 4.7105
LAT = 52.5182


def site(carriageway, *, bearing=None, km=None, lon=LON, lat=LAT):
    return SimpleNamespace(
        effective_carriageway=carriageway,
        vild_bearing=bearing,
        km=km,
        lon=lon,
        lat=lat,
    )


def offset(metres_north=0.0, metres_east=0.0):
    """A position `metres` from (LON, LAT)."""
    return (
        LON + metres_east / (111320.0 * math.cos(math.radians(LAT))),
        LAT + metres_north / 110540.0,
    )


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDb:
    """Minimal stand-in for a session: records queries, returns canned rows.

    Only the raw-SQL road-context lookup gets rows; the ORM speed queries get
    none, so `/speed` exercises its scope handling without needing a database.
    """

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return FakeResult(self.rows if isinstance(statement, TextClause) else [])


# ─── along-track projection ──────────────────────────────────────────────────


def test_along_track_is_positive_ahead_and_negative_behind():
    north_lon, north_lat = offset(metres_north=500)
    ahead, distance = _along_track_m(LON, LAT, 0, north_lon, north_lat)
    assert ahead == pytest.approx(500, abs=1)
    assert distance == pytest.approx(500, abs=1)

    behind, _ = _along_track_m(LON, LAT, 180, north_lon, north_lat)
    assert behind == pytest.approx(-500, abs=1)


def test_along_track_is_zero_without_a_heading():
    north_lon, north_lat = offset(metres_north=500)
    along, distance = _along_track_m(LON, LAT, None, north_lon, north_lat)
    assert along == 0.0
    assert distance == pytest.approx(500, abs=1)


# ─── carriageway resolution ──────────────────────────────────────────────────


def test_no_rows_resolves_to_no_context():
    assert _resolve_road_context([], LON, LAT, 0) is None


def test_heading_picks_the_agreeing_carriageway_over_the_nearest():
    rows = [site("L", bearing=180, km=10.0), site("R", bearing=0, km=10.0)]
    context = _resolve_road_context(rows, LON, LAT, 5)
    assert context["carriageway"] == "R"


def test_known_heading_fails_closed_when_no_bearing_agrees():
    rows = [site("L", bearing=180, km=10.0), site("R", bearing=175, km=10.0)]
    assert _resolve_road_context(rows, LON, LAT, 0) is None


def test_perpendicular_bearings_do_not_resolve_either_carriageway():
    rows = [site("L", bearing=180, km=10.0), site("R", bearing=0, km=10.0)]
    assert _resolve_road_context(rows, LON, LAT, 90) is None


def test_direction_candidate_beyond_the_distance_limit_is_not_used():
    lon, lat = offset(metres_north=1501)
    rows = [site("R", bearing=0, km=12.0, lon=lon, lat=lat)]
    assert _resolve_road_context(rows, LON, LAT, 0) is None


def test_a_sparsely_instrumented_road_still_resolves_its_carriageway():
    # The N205 case: the nearest carriageway-tagged site is 560m away, which the
    # old 500m limit rejected outright, leaving the drive HUD with no context at
    # all on most of the provincial network.
    lon, lat = offset(metres_north=560)
    rows = [site("R", bearing=0, km=None, lon=lon, lat=lat)]
    context = _resolve_road_context(rows, LON, LAT, 0)
    assert context["carriageway"] == "R"
    assert context["anchor_km"] is None


def test_nearest_wins_without_a_heading():
    rows = [site("L", bearing=180, km=10.0), site("R", bearing=0, km=10.0)]
    context = _resolve_road_context(rows, LON, LAT, None)
    assert context["carriageway"] == "L"


def test_rows_without_a_bearing_are_skipped_for_the_direction_check():
    rows = [site("L", bearing=None, km=10.0), site("R", bearing=0, km=12.0)]
    context = _resolve_road_context(rows, LON, LAT, 0)
    assert context["carriageway"] == "R"


# ─── anchor hectometre ───────────────────────────────────────────────────────


def test_anchor_walks_back_from_a_site_ahead_on_carriageway_r():
    # Hectometrering rises with travel on R, so a site 500m ahead at km 12.0
    # puts us at km 11.5.
    lon, lat = offset(metres_north=500)
    rows = [site("R", bearing=0, km=12.0, lon=lon, lat=lat)]
    context = _resolve_road_context(rows, LON, LAT, 0)
    assert context["anchor_km"] == pytest.approx(11.5, abs=0.01)
    assert context["anchor_distance_m"] == pytest.approx(500, abs=1)


def test_anchor_walks_forward_from_a_site_ahead_on_carriageway_l():
    # On L hectometrering falls with travel, so the same site puts us at 12.5.
    lon, lat = offset(metres_north=500)
    rows = [site("L", bearing=0, km=12.0, lon=lon, lat=lat)]
    context = _resolve_road_context(rows, LON, LAT, 0)
    assert context["anchor_km"] == pytest.approx(12.5, abs=0.01)


def test_anchor_uses_a_site_behind_us_too():
    lon, lat = offset(metres_north=-300)
    rows = [site("R", bearing=0, km=12.0, lon=lon, lat=lat)]
    context = _resolve_road_context(rows, LON, LAT, 0)
    assert context["anchor_km"] == pytest.approx(12.3, abs=0.01)


def test_anchor_comes_from_the_chosen_carriageway_only():
    near_lon, near_lat = offset(metres_east=20)
    far_lon, far_lat = offset(metres_north=400)
    rows = [
        site("L", bearing=180, km=99.0, lon=near_lon, lat=near_lat),
        site("R", bearing=0, km=12.0, lon=far_lon, lat=far_lat),
    ]
    context = _resolve_road_context(rows, LON, LAT, 0)
    assert context["carriageway"] == "R"
    assert context["anchor_km"] == pytest.approx(11.6, abs=0.02)
    assert context["anchor_distance_m"] == pytest.approx(400, abs=2)


def test_carriageway_without_any_hectometre_yields_a_null_anchor():
    context = _resolve_road_context([site("R", bearing=0, km=None)], LON, LAT, 0)
    assert context["carriageway"] == "R"
    assert context["anchor_km"] is None
    assert context["anchor_distance_m"] is None


def test_unknown_carriageway_code_yields_a_null_anchor():
    # Only R/L have a defined hectometrering direction; anything else can name
    # the carriageway but cannot place us along it.
    context = _resolve_road_context([site("Op", bearing=0, km=12.0)], LON, LAT, 0)
    assert context["carriageway"] == "Op"
    assert context["anchor_km"] is None


# ─── endpoint ────────────────────────────────────────────────────────────────


def test_endpoint_normalizes_the_road_and_reports_the_context():
    lon, lat = offset(metres_north=500)
    db = FakeDb([site("R", bearing=0, km=12.0, lon=lon, lat=lat)])
    body = get_road_context(db=db, road="a09", lon=LON, lat=LAT, heading=0)
    assert body["road"] == "A9"
    assert body["carriageway"] == "R"
    assert body["anchor_km"] == pytest.approx(11.5, abs=0.01)
    assert body["anchor_distance_m"] == pytest.approx(500, abs=1)
    assert db.calls[0][1]["road"] == "A9"


def test_endpoint_reports_nulls_for_a_road_with_no_sites():
    body = get_road_context(db=FakeDb([]), road="A9", lon=LON, lat=LAT, heading=None)
    assert body == {
        "road": "A9",
        "carriageway": None,
        "anchor_km": None,
        "anchor_distance_m": None,
    }


def test_endpoint_rejects_an_unrecognizable_road():
    with pytest.raises(HTTPException) as excinfo:
        get_road_context(db=FakeDb([]), road="not-a-road", lon=LON, lat=LAT)
    assert excinfo.value.status_code == 400


# ─── /speed scope echo ───────────────────────────────────────────────────────


def call_speed(db, **kwargs):
    """Call the endpoint directly, so every Query() default must be supplied."""
    params = {
        "b": None,
        "road": None,
        "carriageway": None,
        "km_min": None,
        "km_max": None,
        "lon": None,
        "lat": None,
        "heading": None,
        "limit": 500,
    }
    params.update(kwargs)
    return json.loads(get_speed(db=db, **params).body)


def test_speed_echoes_a_null_scope_for_a_bbox_only_request():
    # Regression: `carriageway` is only assigned inside the `road` branch, so a
    # bbox-only request must still find it initialized.
    body = call_speed(FakeDb([]), b=BBox(4.7, 52.5, 4.8, 52.6))
    assert body["road"] is None
    assert body["carriageway"] is None


def test_speed_echoes_the_normalized_road_and_explicit_carriageway():
    body = call_speed(FakeDb([]), road="a09", carriageway="r")
    assert body["road"] == "A9"
    assert body["carriageway"] == "R"


def test_speed_echoes_the_carriageway_it_inferred_from_the_position():
    db = FakeDb([site("L", bearing=180, km=12.0)])
    body = call_speed(db, road="A9", lon=LON, lat=LAT, heading=180)
    assert body["carriageway"] == "L"


def test_speed_requires_a_scope():
    with pytest.raises(HTTPException) as excinfo:
        call_speed(FakeDb([]))
    assert excinfo.value.status_code == 400
