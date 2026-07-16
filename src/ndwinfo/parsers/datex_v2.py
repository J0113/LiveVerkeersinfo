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


def _float(elem, tag: str) -> float | None:
    v = _text(elem, tag)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _attr_int(elem, name: str) -> int | None:
    value = elem.get(name) if elem is not None else None
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _attr_float(elem, name: str) -> float | None:
    value = elem.get(name) if elem is not None else None
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _data_error(elem) -> bool | None:
    """Return an explicit DATEX dataError flag without treating absence as false."""
    value = _text(elem, "dataError") if elem is not None else None
    if value is None:
        return None
    normalized = value.casefold()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
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


def _linear_geom(elem) -> str | None:
    """Build a WKT LINESTRING from a Linear location's start/end coordinates.

    Travel-time segments carry linearByCoordinatesExtension/linearCoordinates
    StartPoint + EndPoint. Returns None when either endpoint is missing.
    """
    def _pt(tag: str) -> tuple[float, float] | None:
        node = elem.find(f".//{T}{tag}/{T}pointCoordinates")
        if node is None:
            return None
        lat_e = node.find(f"{T}latitude")
        lon_e = node.find(f"{T}longitude")
        if lat_e is not None and lon_e is not None and lat_e.text and lon_e.text:
            try:
                return float(lon_e.text), float(lat_e.text)
            except ValueError:
                return None
        return None

    start = _pt("linearCoordinatesStartPoint")
    end = _pt("linearCoordinatesEndPoint")
    if start and end and start != end:
        return f"LINESTRING({start[0]} {start[1]}, {end[0]} {end[1]})"
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
    road = km = carriageway = carriageway_source = None
    n = name or ""
    prefix = site_id.split("_")[0]

    # --- GEO*/RWSTI: "009hrl057760" (3-digit road, 3-char cw, 6-digit metres) ---
    if prefix.startswith("GEO") and re.fullmatch(r"\d{3}[a-z]{3}\d{6}", n):
        road_num = int(n[0:3])
        road = f"A{road_num}" if road_num <= 99 else f"N{road_num}"
        km = round(int(n[6:12]) / 1000.0, 3)
        cw_code = n[3:6]
        if cw_code == "hrr":
            carriageway, carriageway_source = "R", "site_name_hrr"
        elif cw_code == "hrl":
            carriageway, carriageway_source = "L", "site_name_hrl"

    # --- RWS01 MONIBAS: "0091hrl0572ra" (3-digit road + 1-digit subcode, 3-char cw, 4-digit hm, 2-char suffix) ---
    elif prefix == "RWS01" and re.fullmatch(r"\d{4}[a-z]{3}\d{4}[a-z]{2}", n):
        road_num = int(n[0:3])  # subcode at n[3] ignored
        road = f"A{road_num}" if road_num <= 99 else f"N{road_num}"
        km = round(int(n[7:11]) * 0.1, 1)  # hectometres → km
        cw_code = n[4:7]
        if cw_code.startswith("hr"):
            # hrr = hoofdrijbaan rechts (R), hrl = hoofdrijbaan links (L)
            carriageway = "R" if cw_code[2] == "r" else "L"
            carriageway_source = "site_name_hrr" if carriageway == "R" else "site_name_hrl"

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
            if carriageway:
                carriageway_source = "site_id_hrr" if carriageway == "R" else "site_id_hrl"
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
            carriageway_source = "site_name_re_li"

    # Alert-C positive/negative is a VILD chain direction, not an R/L side.
    # Never turn it into a carriageway without separate hectometre evidence.
    return {
        "road": road,
        "carriageway": carriageway,
        "carriageway_source": carriageway_source,
        "km": km,
    }


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
        line_geom = _linear_geom(loc) if loc is not None else None

        name = _multilang(elem, "measurementSiteName")

        # openlrBearing: first openlrLocationReferencePoint bearing
        first_lrp = elem.find(f".//{T}openlrLocationReferencePoint")
        line_attributes = (
            first_lrp.find(f"{T}openlrLineAttributes") if first_lrp is not None else None
        )
        bear_e = (
            line_attributes.find(f"{T}openlrBearing")
            if line_attributes is not None
            else None
        )
        openlr_bearing = int(bear_e.text) if bear_e is not None and bear_e.text else None
        point_along_line = elem.find(f".//{T}openlrPointAlongLine")

        def _deep_text(tag: str) -> str | None:
            found = elem.find(f".//{T}{tag}")
            return found.text.strip() if found is not None and found.text else None

        def _deep_int(tag: str) -> int | None:
            value = _deep_text(tag)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return None

        openlr_data = None
        if point_along_line is not None:
            lrps = []
            for point_tag in (
                "openlrLocationReferencePoint",
                "openlrLastLocationReferencePoint",
            ):
                point = point_along_line.find(f"{T}{point_tag}")
                if point is None:
                    continue
                coord = point.find(f"{T}openlrCoordinate")
                attrs = point.find(f"{T}openlrLineAttributes")
                path = point.find(f"{T}openlrPathAttributes")
                lrps.append({
                    "kind": point_tag,
                    "latitude": _float(coord, "latitude") if coord is not None else None,
                    "longitude": _float(coord, "longitude") if coord is not None else None,
                    "frc": _text(attrs, "openlrFunctionalRoadClass") if attrs is not None else None,
                    "fow": _text(attrs, "openlrFormOfWay") if attrs is not None else None,
                    "bearing": _int(attrs, "openlrBearing") if attrs is not None else None,
                    "lowest_frc_to_next": (
                        _text(path, "openlrLowestFRCToNextLRPoint") if path is not None else None
                    ),
                    "distance_to_next_m": (
                        _int(path, "openlrDistanceToNextLRPoint") if path is not None else None
                    ),
                })
            openlr_data = {"lrps": lrps}

        # alertCDirectionCoded: positive/negative travel direction
        dir_e = elem.find(f".//{T}alertCDirectionCoded")
        alc_dir = dir_e.text if dir_e is not None else None

        # AlertC/TMC primary+secondary location codes (travel-time linear segments)
        def _loc_code(method: str) -> int | None:
            e = elem.find(
                f".//{T}alertCMethod4{method}PointLocation/{T}alertCLocation/{T}specificLocation"
            )
            if e is not None and e.text:
                try:
                    return int(e.text)
                except ValueError:
                    return None
            return None

        tmc_primary = _loc_code("Primary")
        tmc_secondary = _loc_code("Secondary")
        tmc_offset_e = elem.find(f".//{T}offsetDistance/{T}offsetDistance")
        try:
            tmc_offset_m = (
                int(tmc_offset_e.text)
                if tmc_offset_e is not None and tmc_offset_e.text
                else None
            )
        except ValueError:
            tmc_offset_m = None

        location_info = _parse_site_location(site_id, name, alc_dir)

        site: dict = {
            "id": site_id,
            "name": name,
            "equipment_type": _multilang(elem, "measurementEquipmentTypeUsed"),
            "equipment_reference": _text(elem, "measurementEquipmentReference"),
            "computation_method": _text(elem, "computationMethod"),
            "num_lanes": _int(elem, "measurementSiteNumberOfLanes"),
            "side": _text(elem, "measurementSide"),
            "version": int(version_str) if version_str else None,
            "record_version_time": _text(elem, "measurementSiteRecordVersionTime"),
            "road": location_info["road"],
            "carriageway": location_info["carriageway"],
            "carriageway_source": location_info["carriageway_source"],
            "carriageway_type": _deep_text("carriageway"),
            "km": location_info["km"],
            "openlr_bearing": openlr_bearing,
            "openlr_side_of_road": _deep_text("openlrSideOfRoad"),
            "openlr_orientation": _deep_text("openlrOrientation"),
            "openlr_positive_offset_m": _deep_int("openlrPositiveOffset"),
            "openlr_frc": _deep_text("openlrFunctionalRoadClass"),
            "openlr_fow": _deep_text("openlrFormOfWay"),
            "openlr_data": openlr_data,
            "geom": geom,
            "line_geom": line_geom,
            "tmc_primary": tmc_primary,
            "tmc_secondary": tmc_secondary,
            "tmc_direction": alc_dir,
            "tmc_country_code": _deep_text("alertCLocationCountryCode"),
            "tmc_table_number": _deep_text("alertCLocationTableNumber"),
            "tmc_table_version": _deep_text("alertCLocationTableVersion"),
            "tmc_offset_m": tmc_offset_m,
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
                    "accuracy": _float(char, "accuracy"),
                    "vehicle_type": (
                        _text(vc, "vehicleType") if vc is not None else None
                    ),
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
    ``measurement_status`` preserves the distinction made by the Dutch DATEX
    profile between an error, a functioning detector that observed no traffic,
    a valid standstill and an ordinary measurement.  Only the latter two expose
    ``speed_kmh`` as a usable speed observation.
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
            n_incomplete_inputs: int | None = None
            supplier_quality: float | None = None
            computational_method: str | None = None
            data_error: bool | None = None
            measurement_status: str | None = None
            is_usable: bool | None = None

            if "TrafficFlow" in xsi_type:
                value_elem = basic.find(f"{T}vehicleFlow")
                vfr = value_elem.find(f"{T}vehicleFlowRate") if value_elem is not None else None
                n_inputs = _attr_int(value_elem, "numberOfInputValuesUsed")
                n_incomplete_inputs = _attr_int(value_elem, "numberOfIncompleteInputs")
                std_dev = _attr_float(value_elem, "standardDeviation")
                supplier_quality = _attr_float(value_elem, "supplierCalculatedDataQuality")
                computational_method = (
                    value_elem.get("computationalMethod") if value_elem is not None else None
                )
                data_error = _data_error(value_elem)
                if vfr is not None and vfr.text:
                    try:
                        flow = float(vfr.text)
                    except ValueError:
                        pass
                if data_error is True:
                    measurement_status = "error"
                    is_usable = False
                    flow = None
                elif supplier_quality == 0:
                    measurement_status = "quality_rejected"
                    is_usable = False
                    flow = None
                elif flow == 0 and n_incomplete_inputs == 0:
                    measurement_status = "no_traffic"
                    is_usable = False
                elif flow is not None:
                    measurement_status = "measurement"
                    is_usable = True

            elif "TrafficSpeed" in xsi_type:
                avg = basic.find(f"{T}averageVehicleSpeed")
                n_inputs = _attr_int(avg, "numberOfInputValuesUsed")
                n_incomplete_inputs = _attr_int(avg, "numberOfIncompleteInputs")
                std_dev = _attr_float(avg, "standardDeviation")
                supplier_quality = _attr_float(avg, "supplierCalculatedDataQuality")
                computational_method = avg.get("computationalMethod") if avg is not None else None
                data_error = _data_error(avg)
                speed_e = avg.find(f"{T}speed") if avg is not None else None
                raw_speed: float | None = None
                if speed_e is not None and speed_e.text:
                    try:
                        raw_speed = float(speed_e.text)
                    except ValueError:
                        pass

                # The Dutch profile defines -1 as the error sentinel.  Fail
                # closed even when a supplier omitted the required dataError.
                if data_error is True or raw_speed == -1.0:
                    measurement_status = "error"
                    is_usable = False
                elif supplier_quality == 0:
                    measurement_status = "quality_rejected"
                    is_usable = False
                elif (
                    raw_speed == 0.0
                    and n_inputs == 0
                    and n_incomplete_inputs == 0
                ):
                    measurement_status = "no_traffic"
                    is_usable = False
                elif raw_speed == 0.0:
                    measurement_status = "valid_standstill"
                    is_usable = True
                    speed = raw_speed
                elif raw_speed is not None and raw_speed >= 0.0:
                    measurement_status = "measurement"
                    is_usable = True
                    speed = raw_speed

            row = {
                "site_id": site_id,
                "index": int(index),
                "measured_at": measured_at,
                "value_type": value_type,
                "flow_veh_h": flow,
                "speed_kmh": speed,
                "n_inputs": n_inputs,
                "std_dev": std_dev,
                "n_incomplete_inputs": n_incomplete_inputs,
                "supplier_quality": supplier_quality,
                "computational_method": computational_method,
                "data_error": data_error,
                "measurement_status": measurement_status,
                "is_usable": is_usable,
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
