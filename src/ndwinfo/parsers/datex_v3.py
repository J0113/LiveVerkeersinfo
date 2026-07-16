"""DATEX II v3 parsers: situations, DRIPs, parking status, emission zones."""

from __future__ import annotations

from collections.abc import Iterator

from lxml import etree
from shapely.geometry import LineString, Point, Polygon

SIT = "http://datex2.eu/schema/3/situation"
MC  = "http://datex2.eu/schema/3/messageContainer"
LOC = "http://datex2.eu/schema/3/locationReferencing"
COM = "http://datex2.eu/schema/3/common"
VMS = "http://datex2.eu/schema/3/vms"
CZ  = "http://datex2.eu/schema/3/controlledZone"
TRO = "http://datex2.eu/schema/3/trafficRegulation"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

XSIT = f"{{{XSI}}}"
NS = {"sit": SIT, "mc": MC, "loc": LOC, "com": COM, "vms": VMS, "cz": CZ, "tro": TRO}


def _t(ns: str) -> str:
    return f"{{{ns}}}"


def _text_e(elem, path: str) -> str | None:
    e = elem.find(path, NS)
    return e.text.strip() if e is not None and e.text else None


def _bool(s: str | None) -> bool | None:
    if s is None:
        return None
    return s.strip().lower() == "true"


def _number(s: str | None) -> int | float | None:
    if s is None:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _local_type(value: str | None) -> str:
    """Return the local part of either a QName or Clark-notation type."""
    if not value:
        return ""
    if "}" in value:
        return value.rsplit("}", 1)[-1]
    return value.rsplit(":", 1)[-1]


def _local_name(elem) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def _texts_local(elem, name: str) -> list[str]:
    values: list[str] = []
    for child in elem.iter():
        if _local_name(child) == name and child.text and child.text.strip():
            values.append(child.text.strip())
    return values


def _first_local(elem, name: str) -> str | None:
    values = _texts_local(elem, name)
    return values[0] if values else None


def _poslist_to_linestring(poslist: str) -> str | None:
    vals = poslist.split()
    if len(vals) < 4 or len(vals) % 2 != 0:
        return None
    try:
        coords = [(float(vals[i + 1]), float(vals[i])) for i in range(0, len(vals), 2)]
        return LineString(coords).wkt
    except (ValueError, IndexError):
        return None


def _poslist_to_polygon(poslist: str) -> str | None:
    vals = poslist.split()
    if len(vals) < 6 or len(vals) % 2 != 0:
        return None
    try:
        coords = [(float(vals[i + 1]), float(vals[i])) for i in range(0, len(vals), 2)]
        return Polygon(coords).wkt
    except (ValueError, IndexError):
        return None


def _geom_from_location(loc_elem) -> str | None:
    if loc_elem is None:
        return None
    xsi_type = loc_elem.get(XSIT + "type", "")

    # Prefer the route geometry of an itinerary over a reference point embedded
    # elsewhere in that itinerary.
    poslist_e = loc_elem.find(f".//{{{LOC}}}posList")
    if poslist_e is not None and poslist_e.text:
        return _poslist_to_linestring(poslist_e.text)

    lat_e = loc_elem.find(f".//{{{LOC}}}latitude")
    lon_e = loc_elem.find(f".//{{{LOC}}}longitude")
    if lat_e is not None and lon_e is not None and lat_e.text and lon_e.text:
        try:
            return f"POINT({float(lon_e.text)} {float(lat_e.text)})"
        except ValueError:
            pass

    return None


_SUBTYPE_FIELDS = {
    "Accident": "accidentType",
    "AbnormalTraffic": "abnormalTrafficType",
    "ConstructionWorks": "constructionWorkType",
    "GeneralNetworkManagement": "generalNetworkManagementType",
    "GeneralObstruction": "obstructionType",
    "MaintenanceWorks": "maintenanceWorkType",
    "PublicEvent": "publicEventType",
    "ReroutingManagement": "reroutingManagementType",
    "RoadOrCarriagewayOrLaneManagement": "roadOrCarriagewayOrLaneManagementType",
    "SpeedManagement": "speedManagementType",
    "VehicleObstruction": "vehicleObstructionType",
}

_INCIDENT_TYPES = {
    "Accident",
    "AbnormalTraffic",
    "AnimalPresenceObstruction",
    "EnvironmentalObstruction",
    "EquipmentOrSystemFault",
    "GeneralObstruction",
    "NonWeatherRelatedRoadCondition",
    "PoorEnvironmentConditions",
    "VehicleObstruction",
    "WeatherRelatedRoadCondition",
}

_ROADWORK_TYPES = {
    "AuthorityOperation",
    "ConstructionWorks",
    "MaintenanceWorks",
    "PublicEvent",
}

_CLOSURE_SUBTYPES = {
    "carriagewayClosures",
    "laneClosures",
    "roadClosed",
}


def _record_subtype(record, record_type: str) -> str | None:
    field = _SUBTYPE_FIELDS.get(record_type)
    if field:
        return _first_local(record, field)

    # Preserve useful subtype information for a DATEX specialization that is
    # newer than this parser. Cause/status fields are deliberately excluded.
    for child in record:
        name = _local_name(child)
        if name.endswith("Type") and name not in {"causeType", "mobilityType"}:
            if child.text and child.text.strip():
                return child.text.strip()
    return None


def _situation_category(
    record_type: str,
    subtype: str | None,
    safety_related: bool | None,
    fallback: str,
) -> str:
    """Classify a record by its contents, never merely by publication name."""
    if record_type == "SpeedManagement":
        return "speed_limit"
    if record_type == "GeneralNetworkManagement":
        if subtype and "bridge" in subtype.lower():
            return "bridge_opening"
        return "roadworks"
    if record_type == "RoadOrCarriagewayOrLaneManagement":
        return "closure" if subtype in _CLOSURE_SUBTYPES else "roadworks"
    if record_type == "ReroutingManagement":
        return "closure"
    if record_type in _ROADWORK_TYPES:
        return "roadworks"
    if record_type in _INCIDENT_TYPES:
        return "srti" if safety_related else "incident"
    return fallback


def _alert_c(location) -> dict | None:
    alert = next(
        (
            child
            for child in location.iter()
            if _local_name(child) in {"alertCPoint", "alertCLinear"}
        ),
        None,
    )
    if alert is None:
        return None

    def endpoint(kind: str) -> dict | None:
        container = next(
            (
                child
                for child in alert.iter()
                if kind in _local_name(child) and _local_name(child).endswith("PointLocation")
            ),
            None,
        )
        if container is None:
            return None
        code = _first_local(container, "specificLocation")
        offset = _number(_first_local(container, "offsetDistance"))
        if code is None and offset is None:
            return None
        return {"specific_location": code, "offset_distance_m": offset}

    return {
        "type": _local_type(alert.get(XSIT + "type")),
        "country_code": _first_local(alert, "alertCLocationCountryCode"),
        "table_number": _first_local(alert, "alertCLocationTableNumber"),
        "table_version": _first_local(alert, "alertCLocationTableVersion"),
        "direction_coded": _first_local(alert, "alertCDirectionCoded"),
        "affected_direction": _first_local(alert, "alertCAffectedDirection"),
        "primary": endpoint("Primary"),
        "secondary": endpoint("Secondary"),
    }


def _location_contract(loc_elem) -> list[dict]:
    if loc_elem is None:
        return []

    contained = loc_elem.findall(f".//{{{LOC}}}locationContainedInItinerary")
    logical_locations: list[tuple[int | None, object]] = []
    for item in contained:
        location = item.find(f"{{{LOC}}}location")
        if location is not None:
            index = _number(item.get("index"))
            logical_locations.append((index if isinstance(index, int) else None, location))
    if not logical_locations:
        logical_locations = [(None, loc_elem)]

    result: list[dict] = []
    for index, location in logical_locations:
        pos_lists = _texts_local(location, "posList")
        lat = _number(_first_local(location, "latitude"))
        lon = _number(_first_local(location, "longitude"))
        carriageways = _texts_local(location, "carriageway")
        # The outer <carriageway> is a container without text; retain distinct
        # actual enum values in source order.
        carriageways = list(dict.fromkeys(carriageways))
        lanes = []
        for lane in location.findall(f".//{{{LOC}}}lane"):
            lane_contract = {
                "number": _number(_first_local(lane, "laneNumber")),
                "usage": _first_local(lane, "laneUsage"),
                "status": _first_local(lane, "laneStatus"),
                "type": _first_local(lane, "laneType"),
            }
            if any(value is not None for value in lane_contract.values()):
                lanes.append(lane_contract)
        result.append(
            {
                "index": index,
                "type": _local_type(location.get(XSIT + "type")),
                "carriageway": carriageways[0] if carriageways else None,
                "original_number_of_lanes": _number(
                    _first_local(location, "originalNumberOfLanes")
                ),
                "lanes": lanes,
                "lane_usage": list(dict.fromkeys(_texts_local(location, "laneUsage"))),
                "bearing": _number(_first_local(location, "bearing")),
                "point": (
                    {"latitude": lat, "longitude": lon}
                    if lat is not None and lon is not None
                    else None
                ),
                "gml_line_strings": [
                    wkt
                    for pos_list in pos_lists
                    if (wkt := _poslist_to_linestring(pos_list)) is not None
                ],
                "alert_c": _alert_c(location),
            }
        )
    return result


def _clear(elem) -> None:
    elem.clear()
    while elem.getprevious() is not None:
        del elem.getparent()[0]


# ---------------------------------------------------------------------------
# parse_situations — generic for all 6 SituationPublication feeds
# ---------------------------------------------------------------------------


def parse_situations(fileobj, category: str) -> Iterator[dict]:
    """Parse DATEX v3 SituationPublication.

    Yields one dict per situationRecord.
    ``category`` is retained as a fallback for unknown future record types.
    Known records are classified from their actual xsi:type and subtype.
    """
    SIT_T = _t(SIT)

    for _, sit_elem in etree.iterparse(fileobj, events=("end",), tag=f"{SIT_T}situation"):
        sit_id = sit_elem.get("id")
        severity = _text_e(sit_elem, f"{SIT_T}overallSeverity")
        information_status = _text_e(
            sit_elem, f"{SIT_T}headerInformation/{{{COM}}}informationStatus"
        )

        for record in sit_elem.findall(f"{SIT_T}situationRecord"):
            record_id = record.get("id")
            record_type = _local_type(record.get(XSIT + "type"))
            safety_related = _bool(_text_e(record, f"{SIT_T}safetyRelatedMessage"))
            record_subtype = _record_subtype(record, record_type)
            actual_category = _situation_category(
                record_type, record_subtype, safety_related, category
            )

            # Validity window
            vts = record.find(f".//{{{COM}}}validityTimeSpecification")
            valid_from = valid_to = None
            if vts is not None:
                valid_from = _text_e(vts, f"{{{COM}}}overallStartTime")
                valid_to = _text_e(vts, f"{{{COM}}}overallEndTime")
            validity_status = _first_local(record, "validityStatus")
            valid_periods = []
            if vts is not None:
                for period in vts.findall(f".//{{{COM}}}validPeriod"):
                    valid_periods.append(
                        {
                            "start": _text_e(period, f"{{{COM}}}startOfPeriod"),
                            "end": _text_e(period, f"{{{COM}}}endOfPeriod"),
                        }
                    )
            validity = {
                "status": validity_status,
                "overall_start": valid_from,
                "overall_end": valid_to,
                "valid_periods": valid_periods,
            }

            # Source name (first Dutch value)
            source_name: str | None = None
            src_name_e = record.find(f".//{{{COM}}}sourceName")
            if src_name_e is not None:
                val_e = src_name_e.find(f"{{{COM}}}values/{{{COM}}}value")
                source_name = val_e.text.strip() if val_e is not None and val_e.text else None

            # Geometry
            loc_elem = record.find(f"{SIT_T}locationReference")
            geom = _geom_from_location(loc_elem)
            locations = _location_contract(loc_elem)
            carriageways = list(
                dict.fromkeys(
                    location["carriageway"]
                    for location in locations
                    if location["carriageway"]
                )
            )
            bearings = [
                location["bearing"]
                for location in locations
                if location["bearing"] is not None
            ]
            alert_c_locations = [
                location["alert_c"] for location in locations if location["alert_c"]
            ]

            lane_impact = {
                "number_of_lanes_restricted": _number(
                    _first_local(record, "numberOfLanesRestricted")
                ),
                "number_of_operational_lanes": _number(
                    _first_local(record, "numberOfOperationalLanes")
                ),
                "original_number_of_lanes": next(
                    (
                        location["original_number_of_lanes"]
                        for location in locations
                        if location["original_number_of_lanes"] is not None
                    ),
                    None,
                ),
                "lane_usage": list(
                    dict.fromkeys(
                        lane_usage
                        for location in locations
                        for lane_usage in location["lane_usage"]
                    )
                ),
                "lanes": [
                    lane for location in locations for lane in location["lanes"]
                ],
            }
            if not any(
                value
                for key, value in lane_impact.items()
                if key not in {"lane_usage", "lanes"}
            ) and not lane_impact["lane_usage"] and not lane_impact["lanes"]:
                lane_impact = None

            cause = {
                "type": _first_local(record, "causeType"),
                "detailed_type": _first_local(record, "detailedCauseType"),
            }
            if not any(cause.values()):
                cause = None

            # Speed limit (SpeedManagement only)
            speed_limit_kmh: int | None = None
            if "SpeedManagement" in record_type:
                sl_e = record.find(f"{SIT_T}temporarySpeedLimit")
                if sl_e is not None and sl_e.text:
                    try:
                        speed_limit_kmh = int(float(sl_e.text))
                    except ValueError:
                        pass

            row = {
                "id": sit_id,
                "record_id": record_id,
                "category": actual_category,
                "record_type": record_type,
                "record_subtype": record_subtype,
                "record_version": _number(record.get("version")),
                "feed_category": category,
                "severity": severity,
                "probability": _text_e(record, f"{SIT_T}probabilityOfOccurrence"),
                "safety_related": safety_related,
                "source": source_name,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "version_time": _text_e(record, f"{SIT_T}situationRecordVersionTime"),
                "speed_limit_kmh": speed_limit_kmh,
                "carriageway": carriageways[0] if len(carriageways) == 1 else None,
                "carriageways": carriageways,
                "bearing": bearings[0] if bearings else None,
                "alert_c": alert_c_locations[0] if len(alert_c_locations) == 1 else None,
                "alert_c_locations": alert_c_locations,
                "locations": locations,
                "lane_impact": lane_impact,
                "operator_action_status": _first_local(record, "operatorActionStatus"),
                "record_status": _first_local(record, "situationRecordStatus"),
                "validity_status": validity_status,
                "validity": validity,
                "information_status": information_status,
                "cause": cause,
                "geom": geom,
            }
            row["raw"] = {k: v for k, v in row.items() if k != "raw"}
            yield row

        _clear(sit_elem)


# ---------------------------------------------------------------------------
# parse_drip — VmsTablePublication
# ---------------------------------------------------------------------------


def parse_drip(fileobj) -> Iterator[dict]:
    """Parse DATEX v3 VmsTablePublication (dynamic route information panels).

    Yields one dict per VMS. File is ~5MB decompressed so we parse the full
    tree to allow two-pass extraction (locations + live images).
    """
    VMS_T = _t(VMS)
    LOC_T = _t(LOC)
    COM_T = _t(COM)

    root = etree.parse(fileobj).getroot()

    # Pass 1: collect live status (image + working state) keyed by (ctrl_id, vms_index)
    status_map: dict[tuple[str, int], dict] = {}
    for cstat in root.findall(f".//{VMS_T}vmsControllerStatus"):
        ctrl_ref = cstat.find(f"{VMS_T}vmsControllerReference")
        ctrl_id = ctrl_ref.get("id") if ctrl_ref is not None else None
        if not ctrl_id:
            continue
        for vstatus_outer in cstat.findall(f"{VMS_T}vmsStatus"):
            vidx_str = vstatus_outer.get("vmsIndex")
            _vs = vstatus_outer.find(f"{VMS_T}vmsStatus")
            vstatus = _vs if _vs is not None else vstatus_outer
            working_status = _text_e(vstatus, f"{VMS_T}workingStatus")
            # Per-sign last-update time; fall back to the controller-level one.
            status_update_time = (
                _text_e(vstatus, f"{VMS_T}statusUpdateTime")
                or _text_e(cstat, f"{VMS_T}statusUpdateTime")
            )
            image_data = image_format = None
            display_text = None
            msg_inner = vstatus.find(f"{VMS_T}vmsMessage/{VMS_T}vmsMessage")
            if msg_inner is not None:
                img_e = msg_inner.find(f"{VMS_T}image")
                if img_e is not None:
                    image_data = _text_e(img_e, f"{VMS_T}imageData")
                    image_format = _text_e(img_e, f"{VMS_T}imageFormat") or "png"
                # Text-mode panels (TextDisplay): gather non-empty textLine content
                lines = [
                    tl.text.strip()
                    for tl in msg_inner.findall(f".//{VMS_T}textLine")
                    if tl.text and tl.text.strip()
                ]
                if lines:
                    display_text = "\n".join(lines)
            key = (ctrl_id, int(vidx_str) if vidx_str else 0)
            status_map[key] = {
                "working_status": working_status,
                "image_data": image_data,
                "image_format": image_format,
                "display_text": display_text,
                "status_update_time": status_update_time,
            }

    # Pass 2: yield sign rows merged with status
    for ctrl in root.findall(f".//{VMS_T}vmsController"):
        ctrl_id = ctrl.get("id")
        for vms_outer in ctrl.findall(f"{VMS_T}vms"):
            vms_index_str = vms_outer.get("vmsIndex")
            _vms = vms_outer.find(f"{VMS_T}vms")
            vms = _vms if _vms is not None else vms_outer

            desc_val = vms.find(f".//{COM_T}value")
            description = (
                desc_val.text.strip() if desc_val is not None and desc_val.text else None
            )

            bearing: int | None = None
            carriageway: str | None = None
            lat = lon = None
            loc = vms.find(f"{VMS_T}vmsLocation")
            if loc is not None:
                carriageway = _text_e(
                    loc,
                    f"{LOC_T}supplementaryPositionalDescription/"
                    f"{LOC_T}carriageway/{LOC_T}carriageway",
                )
                b_e = loc.find(f".//{LOC_T}bearing")
                lat_e = loc.find(f".//{LOC_T}latitude")
                lon_e = loc.find(f".//{LOC_T}longitude")
                if b_e is not None and b_e.text:
                    try:
                        bearing = int(b_e.text)
                    except ValueError:
                        pass
                if lat_e is not None and lon_e is not None and lat_e.text and lon_e.text:
                    try:
                        lat = float(lat_e.text)
                        lon = float(lon_e.text)
                    except ValueError:
                        pass

            geom = f"POINT({lon} {lat})" if lat is not None and lon is not None else None
            vms_index = int(vms_index_str) if vms_index_str else 0
            status = status_map.get((ctrl_id, vms_index))

            nda_str = _text_e(vms, f"{VMS_T}vmsConfiguration/{VMS_T}numberOfDisplayAreas")
            try:
                num_display_areas = int(nda_str) if nda_str else None
            except ValueError:
                num_display_areas = None

            row = {
                "controller_id": ctrl_id,
                "vms_index": vms_index,
                "description": description,
                "vms_type": _text_e(vms, f"{VMS_T}vmsType"),
                "physical_support": _text_e(vms, f"{VMS_T}physicalSupport"),
                "bearing": bearing,
                "carriageway": carriageway,
                "num_display_areas": num_display_areas,
                "display_text": status.get("display_text") if status else None,
                "geom": geom,
                "message": status,
            }
            row["raw"] = {k: v for k, v in row.items() if k != "raw"}
            yield row


# ---------------------------------------------------------------------------
# parse_parking_status — ParkingStatusPublication (DATEX v3, may differ in ns)
# ---------------------------------------------------------------------------


def parse_parking_status(fileobj) -> Iterator[dict]:
    """Parse DATEX v3 ParkingStatusPublication.

    Yields one dict per parkingRecordStatus. Uses local-name matching to
    handle namespace variations in this feed.
    """

    def local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    def find_local(el, name: str):
        for child in el.iter():
            if local(child.tag) == name:
                return child
        return None

    for _, elem in etree.iterparse(fileobj, events=("end",)):
        if local(elem.tag) != "parkingRecordStatus":
            continue

        pk_ref = None
        for child in elem:
            if local(child.tag) == "parkingRecordReference":
                pk_ref = child.get("id")
                break

        origin_e = find_local(elem, "parkingStatusOriginTime")

        # parkingOccupancy is a CONTAINER; vacant/occupied/pct are its children
        occ_container = find_local(elem, "parkingOccupancy")
        vacant_e = occupied_e = occ_pct_inner_e = None
        if occ_container is not None:
            vacant_e = find_local(occ_container, "parkingNumberOfVacantSpaces")
            occupied_e = find_local(occ_container, "parkingNumberOfOccupiedSpaces")
            # Inner parkingOccupancy element (numeric) is a child of the container
            for child in occ_container:
                if local(child.tag) == "parkingOccupancy":
                    occ_pct_inner_e = child
                    break

        def _safe_float(e) -> float | None:
            if e is None or not e.text:
                return None
            try:
                return float(e.text.strip())
            except ValueError:
                return None

        row = {
            "parking_id": pk_ref,
            "origin_time": (
                origin_e.text.strip() if origin_e is not None and origin_e.text else None
            ),
            "vacant": (
                int(vacant_e.text.strip())
                if vacant_e is not None and vacant_e.text and vacant_e.text.strip()
                else None
            ),
            "occupied": (
                int(occupied_e.text.strip())
                if occupied_e is not None and occupied_e.text and occupied_e.text.strip()
                else None
            ),
            "occupancy_pct": _safe_float(occ_pct_inner_e),
        }
        row["raw"] = dict(row)
        yield row
        # No clear: file is small, and clearing mid-tree could corrupt parse


# ---------------------------------------------------------------------------
# parse_emission_zones — ControlledZoneTablePublication
# ---------------------------------------------------------------------------


def parse_emission_zones(fileobj) -> Iterator[dict]:
    """Parse DATEX v3 ControlledZoneTablePublication (emission zones).

    Yields one dict per urbanVehicleAccessRegulation.
    """
    CZ_T = _t(CZ)
    COM_T = _t(COM)
    LOC_T = _t(LOC)
    TRO_T = _t(TRO)

    for _, zone in etree.iterparse(
        fileobj, events=("end",), tag=f"{CZ_T}urbanVehicleAccessRegulation"
    ):
        zone_id = zone.get("id")

        # Name: first Dutch value
        name_val = zone.find(f".//{COM_T}value")
        name = name_val.text.strip() if name_val is not None and name_val.text else None

        # Polygon geometry from posList
        geom: str | None = None
        poslist_e = zone.find(f".//{LOC_T}posList")
        if poslist_e is not None and poslist_e.text:
            geom = _poslist_to_polygon(poslist_e.text)

        # Issuing authority: trafficRegulationOrder/issuingAuthority/values/value (first)
        authority = _text_e(
            zone,
            f"{CZ_T}trafficRegulationOrder/{TRO_T}issuingAuthority//{COM_T}value",
        )

        row = {
            "id": zone_id,
            "name": name,
            "zone_type": _text_e(zone, f"{CZ_T}controlledZoneType"),
            "status": _text_e(zone, f"{CZ_T}status"),
            "authority": authority,
            "info_url": _text_e(zone, f"{CZ_T}urlForFurtherInformation"),
            "geom": geom,
        }
        row["raw"] = {k: v for k, v in row.items() if k != "raw"}
        yield row
        _clear(zone)
