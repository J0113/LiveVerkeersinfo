"""NDW matrix sign parser (Matrixsignaalinformatie)."""

from __future__ import annotations

from collections.abc import Iterator

from lxml import etree

NVMS = "http://variable_message_sign.trafficmanagementinfo.publicatie.hwn.rws.nl/1.1"
NT = f"{{{NVMS}}}"


def parse_matrix_signs(fileobj) -> Iterator[tuple[dict, dict | None]]:
    """Parse NDW Matrixsignaalinformatie XML.

    Yields (sign_dict, state_dict) per unique sign UUID.
    state_dict is None when no display event was found for that UUID.
    Geometry (geom) is populated from the MSI shapefile in Phase 4 — None here.
    """
    tree = etree.parse(fileobj)

    sign_locations: dict[str, dict] = {}
    sign_states: dict[str, dict] = {}

    for event_elem in tree.findall(f".//{NT}event"):
        uuid_e = event_elem.find(f"{NT}sign_id/{NT}uuid")
        if uuid_e is None or not uuid_e.text:
            continue
        uuid = uuid_e.text.strip()

        ts_state_e = event_elem.find(f"{NT}ts_state")
        ts_state = ts_state_e.text.strip() if ts_state_e is not None and ts_state_e.text else None

        # Location event
        loc = event_elem.find(f"{NT}lanelocation")
        if loc is not None and uuid not in sign_locations:
            road_e = loc.find(f"{NT}road")
            cw_e = loc.find(f"{NT}carriageway")
            lane_e = loc.find(f"{NT}lane")
            km_e = loc.find(f"{NT}km")
            sign_locations[uuid] = {
                "uuid": uuid,
                "road": road_e.text.strip() if road_e is not None and road_e.text else None,
                "carriageway": cw_e.text.strip() if cw_e is not None and cw_e.text else None,
                "lane": int(lane_e.text) if lane_e is not None and lane_e.text else None,
                "km": float(km_e.text) if km_e is not None and km_e.text else None,
                "geom": None,
            }

        # Display (state) event — keep latest ts_state per uuid
        display = event_elem.find(f"{NT}display")
        if display is not None:
            existing = sign_states.get(uuid)
            is_newer = existing is None or (
                ts_state is not None
                and (existing.get("ts_state") is None or ts_state > existing["ts_state"])
            )
            if is_newer:
                # A <display> usually holds one aspect, but can carry several
                # (e.g. lane_open + speedlimit). Capture all; surface the most
                # informative (non-blank) one in the typed columns, keep the
                # full list in raw.
                aspects = [
                    {
                        "aspect_type": child.tag.replace(NT, ""),
                        "value": child.text.strip() if child.text else None,
                        "flashing": child.get("flashing", "false").lower() == "true",
                        "red_ring": child.get("red_ring", "false").lower() == "true",
                    }
                    for child in display
                ]
                primary = next(
                    (a for a in aspects if a["aspect_type"] != "blank"),
                    aspects[0] if aspects else None,
                )

                state = {
                    "uuid": uuid,
                    "ts_state": ts_state,
                    "aspect_type": primary["aspect_type"] if primary else None,
                    "value": primary["value"] if primary else None,
                    "flashing": primary["flashing"] if primary else False,
                    "red_ring": primary["red_ring"] if primary else False,
                }
                if len(aspects) > 1:
                    state["aspects"] = aspects
                sign_states[uuid] = state

    # Yield pairs; signs without a display event still yield with state=None
    for uuid in set(sign_locations) | set(sign_states):
        sign = sign_locations.get(uuid) or {
            "uuid": uuid, "road": None, "carriageway": None, "lane": None, "km": None, "geom": None
        }
        sign["raw"] = {k: v for k, v in sign.items() if k != "raw"}

        state = sign_states.get(uuid)
        if state is not None:
            state["raw"] = {k: v for k, v in state.items() if k != "raw"}

        yield sign, state
