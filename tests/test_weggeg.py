from shapely import from_wkt
from shapely.geometry import LineString

from ndwinfo.parsers.weggeg import lane_count, make_lane_rows


def test_lane_count_uses_larger_end_of_transition():
    assert lane_count({"OMSCHR": "2 -> 3"}) == 3
    assert lane_count({"OMSCHR": None}) == 1


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
