from __future__ import annotations

from io import BytesIO

from ndwinfo.parsers.datex_v3 import parse_situations


MIXED_SITUATION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<mc:messageContainer
  xmlns:mc="http://datex2.eu/schema/3/messageContainer"
  xmlns:sit="http://datex2.eu/schema/3/situation"
  xmlns:loc="http://datex2.eu/schema/3/locationReferencing"
  xmlns:com="http://datex2.eu/schema/3/common"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <mc:payload xsi:type="sit:SituationPublication">
    <sit:situation id="mixed-1">
      <sit:overallSeverity>high</sit:overallSeverity>
      <sit:headerInformation>
        <com:informationStatus>real</com:informationStatus>
      </sit:headerInformation>
      <sit:situationRecord xsi:type="sit:SpeedManagement" id="speed-1" version="4">
        <sit:situationRecordVersionTime>2026-07-16T10:00:00Z</sit:situationRecordVersionTime>
        <sit:probabilityOfOccurrence>certain</sit:probabilityOfOccurrence>
        <sit:validity>
          <com:validityStatus>definedByValidityTimeSpec</com:validityStatus>
          <com:validityTimeSpecification>
            <com:overallStartTime>2026-07-16T09:00:00Z</com:overallStartTime>
            <com:overallEndTime>2026-07-16T12:00:00Z</com:overallEndTime>
          </com:validityTimeSpecification>
        </sit:validity>
        <sit:cause><sit:causeType>roadMaintenance</sit:causeType></sit:cause>
        <sit:locationReference xsi:type="loc:ItineraryByIndexedLocations">
          <loc:locationContainedInItinerary index="0">
            <loc:location xsi:type="loc:LinearLocation">
              <loc:supplementaryPositionalDescription>
                <loc:carriageway>
                  <loc:carriageway>mainCarriageway</loc:carriageway>
                  <loc:originalNumberOfLanes>3</loc:originalNumberOfLanes>
                  <loc:lane><loc:laneUsage>hardShoulder</loc:laneUsage></loc:lane>
                </loc:carriageway>
              </loc:supplementaryPositionalDescription>
              <loc:gmlLineString><loc:posList>52.0 4.0 52.1 4.1</loc:posList></loc:gmlLineString>
            </loc:location>
          </loc:locationContainedInItinerary>
          <loc:locationContainedInItinerary index="1">
            <loc:location xsi:type="loc:LinearLocation">
              <loc:gmlLineString><loc:posList>52.1 4.1 52.2 4.2</loc:posList></loc:gmlLineString>
            </loc:location>
          </loc:locationContainedInItinerary>
          <loc:locationContainedInItinerary index="2">
            <loc:location xsi:type="loc:SingleRoadLinearLocation">
              <loc:alertCLinear xsi:type="loc:AlertCMethod4Linear">
                <loc:alertCLocationCountryCode>8</loc:alertCLocationCountryCode>
                <loc:alertCLocationTableNumber>6.13</loc:alertCLocationTableNumber>
                <loc:alertCLocationTableVersion>A</loc:alertCLocationTableVersion>
                <loc:alertCDirection>
                  <loc:alertCDirectionCoded>negative</loc:alertCDirectionCoded>
                  <loc:alertCAffectedDirection>aligned</loc:alertCAffectedDirection>
                </loc:alertCDirection>
                <loc:alertCMethod4PrimaryPointLocation>
                  <loc:alertCLocation><loc:specificLocation>8802</loc:specificLocation></loc:alertCLocation>
                  <loc:offsetDistance><loc:offsetDistance>500</loc:offsetDistance></loc:offsetDistance>
                </loc:alertCMethod4PrimaryPointLocation>
                <loc:alertCMethod4SecondaryPointLocation>
                  <loc:alertCLocation><loc:specificLocation>8803</loc:specificLocation></loc:alertCLocation>
                  <loc:offsetDistance><loc:offsetDistance>400</loc:offsetDistance></loc:offsetDistance>
                </loc:alertCMethod4SecondaryPointLocation>
              </loc:alertCLinear>
            </loc:location>
          </loc:locationContainedInItinerary>
        </sit:locationReference>
        <sit:operatorActionStatus>implemented</sit:operatorActionStatus>
        <sit:speedManagementType>speedRestrictionInOperation</sit:speedManagementType>
        <sit:temporarySpeedLimit>70.0</sit:temporarySpeedLimit>
      </sit:situationRecord>
      <sit:situationRecord xsi:type="sit:RoadOrCarriagewayOrLaneManagement" id="closure-1" version="2">
        <sit:safetyRelatedMessage>false</sit:safetyRelatedMessage>
        <sit:validity><com:validityStatus>active</com:validityStatus></sit:validity>
        <sit:impact>
          <sit:numberOfLanesRestricted>1</sit:numberOfLanesRestricted>
          <sit:numberOfOperationalLanes>2</sit:numberOfOperationalLanes>
        </sit:impact>
        <sit:locationReference xsi:type="loc:PointLocation">
          <loc:supplementaryPositionalDescription>
            <loc:carriageway>
              <loc:carriageway>exitSlipRoad</loc:carriageway>
              <loc:originalNumberOfLanes>3</loc:originalNumberOfLanes>
            </loc:carriageway>
          </loc:supplementaryPositionalDescription>
          <loc:pointByCoordinates>
            <loc:bearing>191.7</loc:bearing>
            <loc:pointCoordinates><loc:latitude>52.3</loc:latitude><loc:longitude>4.3</loc:longitude></loc:pointCoordinates>
          </loc:pointByCoordinates>
        </sit:locationReference>
        <sit:operatorActionStatus>beingImplemented</sit:operatorActionStatus>
        <sit:roadOrCarriagewayOrLaneManagementType>laneClosures</sit:roadOrCarriagewayOrLaneManagementType>
      </sit:situationRecord>
      <sit:situationRecord xsi:type="sit:GeneralNetworkManagement" id="bridge-1">
        <sit:generalNetworkManagementType>bridgeSwingInOperation</sit:generalNetworkManagementType>
      </sit:situationRecord>
      <sit:situationRecord xsi:type="sit:VehicleObstruction" id="srti-1">
        <sit:safetyRelatedMessage>true</sit:safetyRelatedMessage>
        <sit:vehicleObstructionType>brokenDownVehicle</sit:vehicleObstructionType>
      </sit:situationRecord>
    </sit:situation>
  </mc:payload>
</mc:messageContainer>
"""


def test_mixed_publication_is_classified_per_record_contents():
    rows = list(parse_situations(BytesIO(MIXED_SITUATION_XML), category="incident"))

    assert {row["record_id"]: row["category"] for row in rows} == {
        "speed-1": "speed_limit",
        "closure-1": "closure",
        "bridge-1": "bridge_opening",
        "srti-1": "srti",
    }
    assert {row["feed_category"] for row in rows} == {"incident"}


def test_directional_and_operational_metadata_is_preserved_in_raw_contract():
    rows = {
        row["record_id"]: row
        for row in parse_situations(BytesIO(MIXED_SITUATION_XML), category="incident")
    }

    speed = rows["speed-1"]
    assert speed["record_subtype"] == "speedRestrictionInOperation"
    assert speed["record_version"] == 4
    assert speed["carriageway"] == "mainCarriageway"
    assert speed["operator_action_status"] == "implemented"
    assert speed["validity_status"] == "definedByValidityTimeSpec"
    assert speed["validity"] == {
        "status": "definedByValidityTimeSpec",
        "overall_start": "2026-07-16T09:00:00Z",
        "overall_end": "2026-07-16T12:00:00Z",
        "valid_periods": [],
    }
    assert speed["information_status"] == "real"
    assert speed["cause"] == {"type": "roadMaintenance", "detailed_type": None}
    assert speed["speed_limit_kmh"] == 70
    assert speed["geom"] == "LINESTRING (4 52, 4.1 52.1)"
    assert len(speed["locations"]) == 3
    assert [item["index"] for item in speed["locations"]] == [0, 1, 2]
    assert speed["locations"][0]["original_number_of_lanes"] == 3
    assert speed["locations"][0]["lane_usage"] == ["hardShoulder"]
    assert speed["locations"][0]["lanes"] == [
        {"number": None, "usage": "hardShoulder", "status": None, "type": None}
    ]
    assert speed["locations"][1]["gml_line_strings"] == [
        "LINESTRING (4.1 52.1, 4.2 52.2)"
    ]
    assert speed["alert_c"]["direction_coded"] == "negative"
    assert speed["alert_c"]["primary"] == {
        "specific_location": "8802",
        "offset_distance_m": 500,
    }
    assert speed["alert_c"]["secondary"] == {
        "specific_location": "8803",
        "offset_distance_m": 400,
    }
    assert speed["raw"]["locations"] == speed["locations"]

    closure = rows["closure-1"]
    assert closure["record_subtype"] == "laneClosures"
    assert closure["carriageway"] == "exitSlipRoad"
    assert closure["bearing"] == 191.7
    assert closure["operator_action_status"] == "beingImplemented"
    assert closure["validity_status"] == "active"
    assert closure["lane_impact"] == {
        "number_of_lanes_restricted": 1,
        "number_of_operational_lanes": 2,
        "original_number_of_lanes": 3,
        "lane_usage": [],
        "lanes": [],
    }
    assert closure["geom"] == "POINT(4.3 52.3)"
