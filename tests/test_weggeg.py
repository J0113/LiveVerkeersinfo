from shapely import from_wkt
from shapely.geometry import LineString

from ndwinfo.parsers.weggeg import lane_count, lane_transition_counts, make_lane_rows


def test_lane_count_uses_larger_end_of_transition():
    assert lane_count({"OMSCHR": "2 -> 3"}) == 3
    assert lane_count({"OMSCHR": None}) == 1


def test_lane_transition_counts_preserves_both_ends_and_unknown():
    assert lane_transition_counts({"OMSCHR": "2 -> 3"}) == (2, 3)
    assert lane_transition_counts({"OMSCHR": "3 → 2 rijstroken"}) == (3, 2)
    assert lane_transition_counts({"OMSCHR": "3"}) == (3, 3)
    assert lane_transition_counts({"OMSCHR": "onbekend"}) == (None, None)
    assert lane_transition_counts({"OMSCHR": "vak 2 en vak 3"}) == (None, None)


def test_make_lane_rows_creates_centered_lane_features():
    attrs = {
        "FK_VELD4": "KLK123",
        "OMSCHR": "2 -> 3",
        "WEGNUMMER": "001",
        "KANTCODE": "H",
        "IZI_SIDE": "R",
    }
    rows = make_lane_rows(attrs, LineString([(155000, 463000), (155100, 463000)]))

    assert [row["id"] for row in rows] == ["KLK123:1", "KLK123:2", "KLK123:3"]
    assert all(row["lane_count"] == 3 for row in rows)
    latitudes = [from_wkt(row["geom"]).coords[0][1] for row in rows]
    assert latitudes[0] > latitudes[1] > latitudes[2]
    lane_lengths = [from_wkt(row["geom"]).length for row in rows]
    assert abs(lane_lengths[2] - lane_lengths[0]) < 1e-6
    assert rows[2]["raw"]["lane_transition"] == {
        "source": [2, 3],
        "travel": [2, 3],
        "lane_presence": "source_end",
        "display": "schematic_only",
    }


def test_decreasing_transition_marks_extra_lane_without_inventing_taper_station():
    attrs = {"FK_VELD4": "KLK321", "OMSCHR": "3 -> 2", "KANTCODE": "H"}
    rows = make_lane_rows(attrs, LineString([(155000, 463000), (155100, 463000)]))

    full_lane = from_wkt(rows[0]["geom"])
    ending_lane = from_wkt(rows[2]["geom"])
    assert abs(ending_lane.length - full_lane.length) < 1e-6
    assert rows[2]["raw"]["lane_transition"]["lane_presence"] == "source_start"


def test_lane_one_stays_left_when_traffic_opposes_digitisation():
    attrs = {
        "FK_VELD4": "KLK456",
        "OMSCHR": "3 -> 3",
        "WEGNUMMER": "001",
        "KANTCODE": "T",
        "IZI_SIDE": "L",
    }
    rows = make_lane_rows(attrs, LineString([(155000, 463000), (155100, 463000)]))

    latitudes = [from_wkt(row["geom"]).coords[0][1] for row in rows]
    assert latitudes[0] < latitudes[1] < latitudes[2]


def test_transition_is_reversed_in_travel_metadata_for_t_direction():
    attrs = {
        "FK_VELD4": "KLK789",
        "OMSCHR": "2 -> 3",
        "KANTCODE": "T",
    }
    rows = make_lane_rows(attrs, LineString([(155000, 463000), (155100, 463000)]))

    latitudes = [from_wkt(row["geom"]).coords[0][1] for row in rows]
    assert latitudes[0] < latitudes[1] < latitudes[2]
    assert rows[2]["raw"]["lane_transition"] == {
        "source": [2, 3],
        "travel": [3, 2],
        "lane_presence": "source_end",
        "display": "schematic_only",
    }


def test_unknown_transition_does_not_invent_a_physical_lane():
    attrs = {
        "FK_VELD4": "KLK000",
        "OMSCHR": "onbekend",
        "KANTCODE": "H",
    }
    rows = make_lane_rows(attrs, LineString([(155000, 463000), (155100, 463000)]))

    assert rows == []
