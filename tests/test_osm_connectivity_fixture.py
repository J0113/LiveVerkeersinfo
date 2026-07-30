"""Validation-oracle fixtures extracted from OSM connectivity relations."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "osm_connectivity_relations.json"


def test_connectivity_relation_oracle_preserves_ids_and_raw_mapping():
    relations = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert {item["relation_id"] for item in relations} == {
        9510894,
        9510895,
        9510900,
    }
    assert next(
        item for item in relations if item["relation_id"] == 9510895
    )["connectivity"] == "2:1|3:2"
    assert all(
        item["from_way_id"] and item["via_node_id"] and item["to_way_id"]
        for item in relations
    )
    assert any(not item["supported"] for item in relations)
