"""GeoJSON and OCPI JSON parsers: EV charging points and tariffs."""

from __future__ import annotations

from collections.abc import Iterator

import ijson


def parse_charging_geojson(fileobj) -> Iterator[tuple[dict, list[dict]]]:
    """Parse charging_point_locations.geojson.

    Yields (charge_point_dict, [availability_dicts]) per feature.
    Uses ijson streaming — safe for large files.
    """
    for feature in ijson.items(fileobj, "features.item"):
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates")

        geom: str | None = None
        if coords and len(coords) >= 2:
            try:
                geom = f"POINT({float(coords[0])} {float(coords[1])})"
            except (TypeError, ValueError):
                pass

        cp = {
            "id": feature.get("id"),
            "cpo_id": props.get("cpo_id"),
            "address": props.get("address"),
            "city": props.get("city"),
            "operator_name": props.get("operator_name"),
            "owner_name": props.get("owner_name"),
            "open": props.get("open"),
            "last_updated": props.get("last_updated"),
            "geom": geom,
        }
        cp["raw"] = {k: v for k, v in cp.items() if k != "raw"}

        avails: list[dict] = []
        for idx, av in enumerate(props.get("availabilities") or []):
            avails.append(
                {
                    "cp_id": cp["id"],
                    "idx": idx,
                    "total": av.get("total"),
                    "available": av.get("available"),
                    "power_max": av.get("power_max"),
                    "power_type": av.get("power_type"),
                    "connector_type": av.get("connector_type"),
                    "connector_format": av.get("connector_format"),
                    "tariff_ids": av.get("tariff_ids"),
                }
            )

        yield cp, avails


def parse_ocpi_tariffs(fileobj) -> Iterator[dict]:
    """Parse charging_point_tariffs_ocpi.json (top-level array of tariff objects).

    Yields one dict per tariff.
    """
    for tariff in ijson.items(fileobj, "item"):
        row = {
            "id": tariff.get("id"),
            "currency": tariff.get("currency"),
            "party_id": tariff.get("party_id"),
            "country_code": tariff.get("country_code"),
            "elements": tariff.get("elements"),
            "last_updated": tariff.get("last_updated"),
        }
        row["raw"] = dict(row)
        yield row
