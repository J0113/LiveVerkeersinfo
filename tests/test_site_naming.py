from ndwinfo.site_naming import ndw_roadway_ref


def test_main_carriageway_codes_map_to_the_osm_vocabulary():
    assert ndw_roadway_ref("0091hrl0337ra") == "Li"
    assert ndw_roadway_ref("0091hrr0337ra") == "Re"
    # RWS08 spells the same code in upper case, dash-separated.
    assert ndw_roadway_ref("001-HRL-Amersfoort-Noord 13-c") == "Li"


def test_slip_roads_keep_their_letter():
    assert ndw_roadway_ref("009vwa042571") == "a"
    assert ndw_roadway_ref("0090vwu0348ra") == "u"
    assert ndw_roadway_ref("001vwb041828") == "b"


def test_names_without_a_roadway_code():
    assert ndw_roadway_ref(None) is None
    assert ndw_roadway_ref("") is None
    assert ndw_roadway_ref("ZWN WIU A13L file afrit 18") is None
    assert ndw_roadway_ref("Zwolseweg_ZIJWGN377HMP201_DerdeSchansweg") is None


def test_the_code_is_a_token_not_a_fragment_of_a_word():
    # Only a standalone code counts: letters either side mean this is a word
    # that happens to contain the letters, not a carriageway code.
    assert ndw_roadway_ref("Achrlaan") is None
    assert ndw_roadway_ref("vwaarde") is None
    assert ndw_roadway_ref("0010vwa0057ra") == "a"
