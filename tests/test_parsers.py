"""Unit tests for all NDW parsers against sample/downloaded files."""

from __future__ import annotations

from pathlib import Path

import pytest

from ndwinfo.download import open_feed

SAMPLES = Path("data/samples")
DATA = Path("data")


def requires_sample(filename: str):
    """Skip optional integration fixtures instead of failing a clean checkout."""
    return pytest.mark.skipif(
        not (SAMPLES / filename).exists(),
        reason=f"optional parser sample {filename} not downloaded",
    )


# ---------------------------------------------------------------------------
# DATEX v2
# ---------------------------------------------------------------------------


@requires_sample("trafficspeed.xml.gz")
def test_parse_trafficspeed():
    from ndwinfo.parsers.datex_v2 import parse_trafficspeed

    with open_feed(SAMPLES / "trafficspeed.xml.gz") as f:
        rows = list(parse_trafficspeed(f))

    assert len(rows) > 100, "expected many speed/flow rows"
    r = rows[0]
    assert r["site_id"], "site_id must be non-empty"
    assert isinstance(r["index"], int), "index must be int"
    assert r["measured_at"] is not None, "measured_at must be present"
    assert r["value_type"] in ("TrafficFlow", "TrafficSpeed"), f"unexpected type: {r['value_type']}"
    # At least some rows should have non-null values
    non_null = [x for x in rows if x["flow_veh_h"] is not None or x["speed_kmh"] is not None]
    assert len(non_null) > 0, "all values are null — check -1 sentinel handling"


@requires_sample("traveltime.xml.gz")
def test_parse_traveltime():
    from ndwinfo.parsers.datex_v2 import parse_traveltime

    with open_feed(SAMPLES / "traveltime.xml.gz") as f:
        rows = list(parse_traveltime(f))

    assert len(rows) > 10
    r = rows[0]
    assert r["segment_id"], "segment_id must be non-empty"
    assert isinstance(r["index"], int)
    assert r["duration_s"] is not None, "duration_s must be present"
    assert r["duration_s"] > 0, "duration must be positive"
    # ref_duration_s may be absent in some rows but should appear in most
    with_ref = [x for x in rows if x["ref_duration_s"] is not None]
    assert len(with_ref) > 0, "no reference durations found"


@pytest.mark.skipif(
    not (DATA / "Truckparking_Parking_Table.xml").exists(),
    reason="Truckparking_Parking_Table.xml not downloaded",
)
def test_parse_truckparking_table():
    from ndwinfo.parsers.datex_v2 import parse_truckparking_table

    with open(DATA / "Truckparking_Parking_Table.xml", "rb") as f:
        rows = list(parse_truckparking_table(f))

    assert len(rows) > 0, "no parking records parsed"
    r = rows[0]
    assert r["id"], "parking id must be non-empty"
    assert r["name"] or r["geom"] or r["capacity"], "at least one field should be populated"


# ---------------------------------------------------------------------------
# DATEX v3 — situations
# ---------------------------------------------------------------------------


@requires_sample("actueel_beeld.xml.gz")
def test_parse_situations_actueel_beeld():
    from ndwinfo.parsers.datex_v3 import parse_situations

    with open_feed(SAMPLES / "actueel_beeld.xml.gz") as f:
        rows = list(parse_situations(f, category="incident"))

    assert len(rows) > 0, "no situations parsed"
    r = rows[0]
    assert r["id"], "situation id required"
    assert r["record_type"], "record_type required"
    # actueel_beeld is a mixed publication. Classification must follow each
    # record's actual type/subtype rather than this parser fallback argument.
    assert any(row["category"] != "incident" for row in rows)
    speed_records = [row for row in rows if row["record_type"] == "SpeedManagement"]
    assert speed_records
    assert all(row["category"] == "speed_limit" for row in speed_records)


@requires_sample("veiligheidsgerelateerde_berichten_srti.xml.gz")
def test_parse_situations_srti():
    from ndwinfo.parsers.datex_v3 import parse_situations

    with open_feed(SAMPLES / "veiligheidsgerelateerde_berichten_srti.xml.gz") as f:
        rows = list(parse_situations(f, category="srti"))

    # SRTI may be empty at any given moment — just assert no crash and check structure if non-empty
    for r in rows:
        assert r["category"] == "srti"
        assert r["id"]


@requires_sample("planningsfeed_brugopeningen.xml.gz")
def test_parse_situations_bridge_openings():
    from ndwinfo.parsers.datex_v3 import parse_situations

    with open_feed(SAMPLES / "planningsfeed_brugopeningen.xml.gz") as f:
        rows = list(parse_situations(f, category="bridge_opening"))

    assert len(rows) > 0
    r = rows[0]
    assert r["id"]
    assert r["valid_from"] is not None, "bridge openings must have validity start"


@requires_sample("tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz")
def test_parse_situations_closures():
    from ndwinfo.parsers.datex_v3 import parse_situations

    with open_feed(SAMPLES / "tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz") as f:
        rows = list(parse_situations(f, category="closure"))

    assert len(rows) > 0
    r = rows[0]
    assert r["id"]


@requires_sample("tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz")
def test_parse_situations_speed_limits():
    from ndwinfo.parsers.datex_v3 import parse_situations

    with open_feed(SAMPLES / "tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz") as f:
        rows = list(parse_situations(f, category="speed_limit"))

    assert len(rows) > 0
    r = rows[0]
    assert r["id"]
    assert r["record_type"] == "SpeedManagement", (
        f"expected SpeedManagement, got {r['record_type']}"
    )
    with_limit = [x for x in rows if x["speed_limit_kmh"] is not None]
    assert len(with_limit) > 0, "no speed_limit_kmh values found"


@requires_sample("dynamische_route_informatie_paneel.xml.gz")
def test_parse_drip():
    from ndwinfo.parsers.datex_v3 import parse_drip

    with open_feed(SAMPLES / "dynamische_route_informatie_paneel.xml.gz") as f:
        rows = list(parse_drip(f))

    assert len(rows) > 0
    r = rows[0]
    assert r["controller_id"], "controller_id required"
    assert isinstance(r["vms_index"], int)
    assert r["geom"] is not None, "all DRIPs should have location"
    assert r["geom"].startswith("POINT("), f"unexpected geom: {r['geom']}"


@requires_sample("emissiezones.xml.gz")
def test_parse_emission_zones():
    from ndwinfo.parsers.datex_v3 import parse_emission_zones

    with open_feed(SAMPLES / "emissiezones.xml.gz") as f:
        rows = list(parse_emission_zones(f))

    assert len(rows) > 0
    r = rows[0]
    assert r["id"], "emission zone id required"
    assert r["name"], "emission zone name required"
    assert r["geom"] is not None, "emission zone must have polygon geometry"
    assert "POLYGON" in r["geom"].upper(), f"expected POLYGON, got: {r['geom'][:60]}"


@pytest.mark.skipif(
    not (DATA / "Truckparking_Parking_Status.xml").exists(),
    reason="Truckparking_Parking_Status.xml not downloaded",
)
def test_parse_parking_status():
    from ndwinfo.parsers.datex_v3 import parse_parking_status

    with open(DATA / "Truckparking_Parking_Status.xml", "rb") as f:
        rows = list(parse_parking_status(f))

    assert len(rows) > 0, "no parking status rows parsed"
    r = rows[0]
    assert r["parking_id"], "parking_id required"


# ---------------------------------------------------------------------------
# NDW VMS
# ---------------------------------------------------------------------------


@requires_sample("Matrixsignaalinformatie.xml.gz")
def test_parse_matrix_signs():
    from ndwinfo.parsers.ndw_vms import parse_matrix_signs

    with open_feed(SAMPLES / "Matrixsignaalinformatie.xml.gz") as f:
        pairs = list(parse_matrix_signs(f))

    assert len(pairs) > 100, "expected many MSI signs"
    signs_with_loc = [s for s, _ in pairs if s.get("road")]
    states = [st for _, st in pairs if st is not None]
    assert len(signs_with_loc) > 0, "no signs with location data"
    assert len(states) > 0, "no display states found"

    state = states[0]
    assert state["uuid"], "state uuid required"
    assert state["aspect_type"], "aspect_type required"


# ---------------------------------------------------------------------------
# GeoJSON + OCPI
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (DATA / "charging_point_locations.geojson.gz").exists(),
    reason="charging_point_locations.geojson.gz not downloaded",
)
def test_parse_charging_geojson():
    from ndwinfo.parsers.geojson_ocpi import parse_charging_geojson

    with open_feed(DATA / "charging_point_locations.geojson.gz") as f:
        pairs = list(parse_charging_geojson(f))

    assert len(pairs) > 100
    cp, avails = pairs[0]
    assert cp["id"], "charge point id required"
    assert cp["geom"] is not None, "charge point must have geometry"
    assert cp["geom"].startswith("POINT("), f"unexpected geom: {cp['geom']}"
    assert isinstance(avails, list)


@pytest.mark.skipif(
    not (DATA / "charging_point_tariffs_ocpi.json.gz").exists(),
    reason="charging_point_tariffs_ocpi.json.gz not downloaded",
)
def test_parse_ocpi_tariffs():
    from ndwinfo.parsers.geojson_ocpi import parse_ocpi_tariffs

    with open_feed(DATA / "charging_point_tariffs_ocpi.json.gz") as f:
        rows = list(parse_ocpi_tariffs(f))

    assert len(rows) > 0
    r = rows[0]
    assert r["id"], "tariff id required"
    assert r["currency"], "currency required"
    assert r["elements"] is not None, "elements (JSONB) required"
