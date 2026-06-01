"""DATEX II v2 parsers: measurement site table, traffic speed/flow, travel time, truck parking."""

from __future__ import annotations

import re
from collections.abc import Iterator

from lxml import etree

D2 = "http://datex2.eu/schema/2/2_0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
T = f"{{{D2}}}"
XSIT = f"{{{XSI}}}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(elem, tag: str) -> str | None:
    child = elem.find(f"{T}{tag}")
    return child.text.strip() if child is not None and child.text else None


def _int(elem, tag: str) -> int | None:
    v = _text(elem, tag)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _latlon(elem) -> str | None:
    """Find first latitude/longitude pair anywhere inside elem → WKT POINT."""
    lat_e = elem.find(f".//{T}latitude")
    lon_e = elem.find(f".//{T}longitude")
    if lat_e is not None and lon_e is not None and lat_e.text and lon_e.text:
        try:
            return f"POINT({float(lon_e.text)} {float(lat_e.text)})"
        except ValueError:
            pass
    return None


def _multilang(elem, tag: str) -> str | None:
    """Extract text from <tag><values><value lang="nl">…</value></values></tag>."""
    child = elem.find(f"{T}{tag}")
    if child is None:
        return None
    val = child.find(f".//{T}value")
    return val.text.strip() if val is not None and val.text else None


def _parse_site_location(site_id: str, name: str | None, alc_dir: str | None) -> dict:
    """Extract road, carriageway (R/L), and km from site_id + name.

    Covers three provider families that carry structured data:
      - GEO*/RWSTI: 12-char name code (RWS highway loop sensors)
      - RWS01 MONIBAS: 13-char name code (MONIBAS aggregate sensors)
      - RWS08: road+HRL/HRR+km encoded in the site_id
      - Provincial (PZH/PFR/etc.): "N457 hmp 4.75 Re" in name
    """
    road = km = carriageway = None
    n = name or ""
    prefix = site_id.split("_")[0]

    # --- GEO*/RWSTI: "009hrl057760" (3-digit road, 3-char cw, 6-digit metres) ---
    if prefix.startswith("GEO") and re.fullmatch(r"\d{3}[a-z]{3}\d{6}", n):
        road_num = int(n[0:3])
        road = f"A{road_num}" if road_num <= 99 else f"N{road_num}"
        km = round(int(n[6:12]) / 1000.0, 3)
        carriageway = "R" if alc_dir == "positive" else "L" if alc_dir == "negative" else None

    # --- RWS01 MONIBAS: "0091hrl0572ra" (3-digit road + 1-digit subcode, 3-char cw, 4-digit hm, 2-char suffix) ---
    elif prefix == "RWS01" and re.fullmatch(r"\d{4}[a-z]{3}\d{4}[a-z]{2}", n):
        road_num = int(n[0:3])  # subcode at n[3] ignored
        road = f"A{road_num}" if road_num <= 99 else f"N{road_num}"
        km = round(int(n[7:11]) * 0.1, 1)  # hectometres → km
        cw_code = n[4:7]
        if cw_code.startswith("hr"):
            # hrr = hoofdrijbaan rechts (R), hrl = hoofdrijbaan links (L)
            carriageway = "R" if cw_code[2] == "r" else "L"
        elif alc_dir == "positive":
            carriageway = "R"
        elif alc_dir == "negative":
            carriageway = "L"

    # --- RWS08: id = "RWS08_6_HRL_096.6_1" or "RWS08_A30_HRL_021.8_1" ---
    elif prefix == "RWS08":
        parts = site_id.split("_")
        if len(parts) >= 4:
            rp, cp, kp = parts[1], parts[2], parts[3]
            digits = re.sub(r"\D", "", rp)
            if digits:
                n2 = int(digits)
                road = rp if rp[0].isalpha() else (f"A{n2}" if n2 <= 99 else f"N{n2}")
            carriageway = "R" if "HRR" in cp.upper() else "L" if "HRL" in cp.upper() else None
            try:
                km = float(kp)
            except (ValueError, TypeError):
                pass

    # --- Provincial: "N457 hmp 4.75 Re" / "N457 km 4.75 Li" ---
    else:
        m = re.match(r"^([AN]\s*\d+[a-zA-Z]?)\s+(?:[Hh]mp|[Kk]m)\s+([\d.,]+)\s+(Re|Li)", n)
        if m:
            road = m.group(1).replace(" ", "")
            km = round(float(m.group(2).replace(",", ".")), 3)
            carriageway = "R" if m.group(3) == "Re" else "L"

    return {"road": road, "carriageway": carriageway, "km": km}


def _clear(elem) -> None:
    """Free memory: clear element and delete preceding siblings."""
    elem.clear()
    while elem.getprevious() is not None:
        del elem.getparent()[0]


# ---------------------------------------------------------------------------
# parse_measurement_site_table
# ---------------------------------------------------------------------------


def parse_measurement_site_table(fileobj) -> Iterator[tuple[dict, list[dict]]]:
    """Parse DATEX v2 MeasurementSiteTable.

    Yields (site_dict, [characteristic_dicts]) per site.
    """
    for _, elem in etree.iterparse(fileobj, events=("end",), tag=f"{T}measurementSiteRecord"):
        site_id = elem.get("id")
        version_str = elem.get("version")

        loc = elem.find(f"{T}measurementSiteLocation")
        geom = _latlon(loc) if loc is not None else None

        name = _multilang(elem, "measurementSiteName")

        # openlrBearing: first openlrLocationReferencePoint bearing
        bear_e = elem.find(f".//{T}openlrLocationReferencePoint/{T}openlrLineAttributes/{T}openlrBearing")
        openlr_bearing = int(bear_e.text) if bear_e is not None and bear_e.text else None

        # alertCDirectionCoded: positive/negative travel direction
        dir_e = elem.find(f".//{T}alertCDirectionCoded")
        alc_dir = dir_e.text if dir_e is not None else None

        location_info = _parse_site_location(site_id, name, alc_dir)

        site: dict = {
            "id": site_id,
            "name": name,
            "equipment_type": _multilang(elem, "measurementEquipmentTypeUsed"),
            "num_lanes": _int(elem, "measurementSiteNumberOfLanes"),
            "side": _text(elem, "measurementSide"),
            "version": int(version_str) if version_str else None,
            "record_version_time": _text(elem, "measurementSiteRecordVersionTime"),
            "road": location_info["road"],
            "carriageway": location_info["carriageway"],
            "km": location_info["km"],
            "openlr_bearing": openlr_bearing,
            "geom": geom,
        }
        site["raw"] = {k: v for k, v in site.items() if k != "raw"}

        chars: list[dict] = []
        for char_outer in elem.findall(f"{T}measurementSpecificCharacteristics"):
            index = char_outer.get("index")
            # Actual content is in a nested element with the same tag name
            char = char_outer.find(f"{T}measurementSpecificCharacteristics")
            if char is None:
                char = char_outer

            # "lane1" → 1, "lane2" → 2, etc.
            lane_str = _text(char, "specificLane")
            lane: int | None = None
            if lane_str and lane_str.startswith("lane"):
                try:
                    lane = int(lane_str[4:])
                except ValueError:
                    pass

            # "trafficFlow" → "TrafficFlow", "trafficSpeed" → "TrafficSpeed"
            raw_vtype = _text(char, "specificMeasurementValueType")
            value_type: str | None = (
                raw_vtype[0].upper() + raw_vtype[1:] if raw_vtype else None
            )

            # Vehicle length bounds from lengthCharacteristic elements.
            # anyVehicle has neither bound → both stay None, used later to
            # identify the all-vehicles aggregate measurement.
            veh_min = veh_max = None
            vc = char.find(f"{T}specificVehicleCharacteristics")
            if vc is not None:
                for lc in vc.findall(f"{T}lengthCharacteristic"):
                    op = lc.findtext(f"{T}comparisonOperator")
                    vl_text = lc.findtext(f"{T}vehicleLength")
                    if op and vl_text:
                        try:
                            vl = float(vl_text)
                            if op in ("lessThan", "lessThanOrEqualTo"):
                                veh_max = vl
                            elif op in ("greaterThan", "greaterThanOrEqualTo"):
                                veh_min = vl
                        except ValueError:
                            pass

            chars.append(
                {
                    "site_id": site_id,
                    "index": int(index) if index else None,
                    "lane": lane,
                    "period_s": _int(char, "period"),
                    "value_type": value_type,
                    "veh_length_min": veh_min,
                    "veh_length_max": veh_max,
                }
            )

        yield site, chars
        _clear(elem)


# ---------------------------------------------------------------------------
# parse_trafficspeed
# ---------------------------------------------------------------------------


def parse_trafficspeed(fileobj) -> Iterator[dict]:
    """Parse DATEX v2 MeasuredDataPublication (flow + speed).

    Yields one dict per (site_id, index).
    speed_kmh is None when speed == -1 or numberOfInputValuesUsed == 0.
    """
    for _, elem in etree.iterparse(fileobj, events=("end",), tag=f"{T}siteMeasurements"):
        site_ref = elem.find(f"{T}measurementSiteReference")
        site_id = site_ref.get("id") if site_ref is not None else None
        measured_at = _text(elem, "measurementTimeDefault")

        for mv_outer in elem.findall(f"{T}measuredValue"):
            index = mv_outer.get("index")
            if index is None:
                continue

            # Structure: outer measuredValue[@index] → inner measuredValue → basicData
            mv_inner = mv_outer.find(f"{T}measuredValue")
            if mv_inner is None:
                continue
            basic = mv_inner.find(f"{T}basicData")
            if basic is None:
                continue

            xsi_type = basic.get(f"{XSIT}type", "")
            # Strip any namespace/prefix
            value_type = xsi_type.rsplit(":", 1)[-1] if ":" in xsi_type else xsi_type

            flow: float | None = None
            speed: float | None = None
            n_inputs: int | None = None
            std_dev: float | None = None

            if "TrafficFlow" in xsi_type:
                vfr = basic.find(f".//{T}vehicleFlowRate")
                if vfr is not None and vfr.text:
                    try:
                        flow = float(vfr.text)
                    except ValueError:
                        pass

            elif "TrafficSpeed" in xsi_type:
                avg = basic.find(f"{T}averageVehicleSpeed")
                n_str = avg.get("numberOfInputValuesUsed", "1") if avg is not None else "1"
                sd_str = avg.get("standardDeviation") if avg is not None else None
                try:
                    n_inputs = int(n_str) if n_str else None
                except ValueError:
                    n_inputs = None
                try:
                    std_dev = float(sd_str) if sd_str else None
                except ValueError:
                    std_dev = None
                speed_e = basic.find(f".//{T}speed")
                if speed_e is not None and speed_e.text:
                    try:
                        s = float(speed_e.text)
                        n = n_inputs if n_inputs is not None else 1
                        if s != -1.0 and n > 0:
                            speed = s
                    except ValueError:
                        pass

            row = {
                "site_id": site_id,
                "index": int(index),
                "measured_at": measured_at,
                "value_type": value_type,
                "flow_veh_h": flow,
                "speed_kmh": speed,
                "n_inputs": n_inputs,
                "std_dev": std_dev,
            }
            row["raw"] = dict(row)
            yield row

        _clear(elem)


# ---------------------------------------------------------------------------
# parse_traveltime
# ---------------------------------------------------------------------------


def parse_traveltime(fileobj) -> Iterator[dict]:
    """Parse DATEX v2 MeasuredDataPublication (travel times).

    Yields one dict per (segment_id, index).
    """
    for _, elem in etree.iterparse(fileobj, events=("end",), tag=f"{T}siteMeasurements"):
        site_ref = elem.find(f"{T}measurementSiteReference")
        segment_id = site_ref.get("id") if site_ref is not None else None
        measured_at = _text(elem, "measurementTimeDefault")

        for mv_outer in elem.findall(f"{T}measuredValue"):
            index = mv_outer.get("index")
            if index is None:
                continue

            mv_inner = mv_outer.find(f"{T}measuredValue")
            if mv_inner is None:
                continue
            basic = mv_inner.find(f"{T}basicData")
            if basic is None:
                continue

            xsi_type = basic.get(f"{XSIT}type", "")
            if "TravelTime" not in xsi_type:
                continue

            travel_time_type = _text(basic, "travelTimeType")

            # Live duration
            tt_elem = basic.find(f"{T}travelTime")
            duration_s = accuracy = None
            n_inputs = None
            quality = None
            if tt_elem is not None:
                dur_e = tt_elem.find(f"{T}duration")
                duration_s = float(dur_e.text) if dur_e is not None and dur_e.text else None
                acc_s = tt_elem.get("accuracy")
                accuracy = float(acc_s) if acc_s else None
                n_s = tt_elem.get("numberOfInputValuesUsed")
                n_inputs = int(n_s) if n_s else None
                quality = tt_elem.get("supplierCalculatedDataQuality")

            # Reference (free-flow) duration — measuredValueExtension is a SIBLING of basicData
            ref_duration_s = None
            ref_block = mv_inner.find(f".//{T}basicDataReferenceValue")
            if ref_block is not None:
                ref_dur_e = ref_block.find(f".//{T}duration")
                ref_duration_s = (
                    float(ref_dur_e.text) if ref_dur_e is not None and ref_dur_e.text else None
                )

            row = {
                "segment_id": segment_id,
                "index": int(index),
                "measured_at": measured_at,
                "travel_time_type": travel_time_type,
                "duration_s": duration_s,
                "ref_duration_s": ref_duration_s,
                "accuracy": accuracy,
                "n_inputs": n_inputs,
                "quality": quality,
            }
            row["raw"] = dict(row)
            yield row

        _clear(elem)


# ---------------------------------------------------------------------------
# parse_truckparking_table
# ---------------------------------------------------------------------------


def parse_truckparking_table(fileobj) -> Iterator[dict]:
    """Parse DATEX v2 ParkingTablePublication.

    Yields one dict per parking record.
    """
    for _, elem in etree.iterparse(fileobj, events=("end",), tag=f"{T}parkingRecord"):
        pk_id = elem.get("id")

        # Operator: search common DATEX v2 name fields inside <operator>
        operator: str | None = None
        op_e = elem.find(f"{T}operator")
        if op_e is not None:
            for tag in ("contactDetailsName", "name", "organisationName"):
                name_e = op_e.find(f".//{T}{tag}")
                if name_e is not None and name_e.text:
                    operator = name_e.text.strip()
                    break

        # Capacity: try several possible field names
        capacity: int | None = None
        for tag in (
            "numberOfParkingSpaces",
            "totalParkingCapacity",
            "parkingCapacity",
            "totalNumberOfParkingSpaces",
        ):
            cap_e = elem.find(f".//{T}{tag}")
            if cap_e is not None and cap_e.text:
                try:
                    capacity = int(cap_e.text)
                    break
                except ValueError:
                    pass

        loc = elem.find(f"{T}parkingLocation")
        geom = _latlon(loc) if loc is not None else None

        row = {
            "id": pk_id,
            "name": _text(elem, "parkingName"),
            "operator": operator,
            "capacity": capacity,
            "geom": geom,
        }
        row["raw"] = dict(row)
        yield row
        _clear(elem)
