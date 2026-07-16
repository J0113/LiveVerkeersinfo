from io import BytesIO

from ndwinfo.parsers.datex_v2 import _parse_site_location, parse_measurement_site_table


def test_alert_c_direction_is_never_guessed_as_carriageway_side():
    connector = _parse_site_location("GEO0B_TEST", "009vwb058082", "positive")
    unstructured = _parse_site_location("PNH02_PNHTI516r", "Wormerveer N203", "negative")
    explicit = _parse_site_location("GEO0B_TEST", "009hrl058082", "positive")

    assert connector["carriageway"] is None
    assert unstructured["carriageway"] is None
    assert explicit["carriageway"] == "L"
    assert explicit["carriageway_source"] == "site_name_hrl"


def test_measurement_site_preserves_location_reference_and_quality_metadata():
    xml = b'''<root xmlns="http://datex2.eu/schema/2/2_0">
      <measurementSiteRecord id="PNH02_TEST" version="3">
        <measurementSiteRecordVersionTime>2026-07-16T09:00:00Z</measurementSiteRecordVersionTime>
        <computationMethod>arithmeticAverageOfSamplesInATimePeriod</computationMethod>
        <measurementEquipmentReference>loop-42</measurementEquipmentReference>
        <measurementSiteName><values><value>N201 hmp 7.4 Re</value></values></measurementSiteName>
        <measurementSiteNumberOfLanes>1</measurementSiteNumberOfLanes>
        <measurementSpecificCharacteristics index="1"><measurementSpecificCharacteristics>
          <accuracy>95</accuracy><period>60</period><specificLane>lane1</specificLane>
          <specificMeasurementValueType>trafficSpeed</specificMeasurementValueType>
          <specificVehicleCharacteristics><vehicleType>anyVehicle</vehicleType></specificVehicleCharacteristics>
        </measurementSpecificCharacteristics></measurementSpecificCharacteristics>
        <measurementSiteLocation>
          <locationForDisplay><latitude>52.1</latitude><longitude>4.5</longitude></locationForDisplay>
          <supplementaryPositionalDescription><affectedCarriagewayAndLanes>
            <carriageway>mainCarriageway</carriageway>
          </affectedCarriagewayAndLanes></supplementaryPositionalDescription>
          <alertCPoint><alertCLocationCountryCode>8</alertCLocationCountryCode>
            <alertCLocationTableNumber>6.13</alertCLocationTableNumber>
            <alertCLocationTableVersion>A</alertCLocationTableVersion>
            <alertCDirection><alertCDirectionCoded>positive</alertCDirectionCoded></alertCDirection>
            <alertCMethod4PrimaryPointLocation><alertCLocation><specificLocation>22406</specificLocation></alertCLocation>
              <offsetDistance><offsetDistance>1130</offsetDistance></offsetDistance>
            </alertCMethod4PrimaryPointLocation>
          </alertCPoint>
          <pointExtension><openlrExtendedPoint><openlrPointLocationReference><openlrPointAlongLine>
            <openlrSideOfRoad>onRoadOrUnknown</openlrSideOfRoad>
            <openlrOrientation>noOrientationOrUnknown</openlrOrientation><openlrPositiveOffset>639</openlrPositiveOffset>
            <openlrLocationReferencePoint><openlrCoordinate><latitude>52.0</latitude><longitude>4.4</longitude></openlrCoordinate>
              <openlrLineAttributes><openlrFunctionalRoadClass>FRC2</openlrFunctionalRoadClass>
                <openlrFormOfWay>multipleCarriageway</openlrFormOfWay><openlrBearing>296</openlrBearing>
              </openlrLineAttributes><openlrPathAttributes><openlrDistanceToNextLRPoint>961</openlrDistanceToNextLRPoint></openlrPathAttributes>
            </openlrLocationReferencePoint>
            <openlrLastLocationReferencePoint><openlrCoordinate><latitude>52.2</latitude><longitude>4.6</longitude></openlrCoordinate>
              <openlrLineAttributes><openlrFunctionalRoadClass>FRC2</openlrFunctionalRoadClass><openlrFormOfWay>multipleCarriageway</openlrFormOfWay><openlrBearing>116</openlrBearing></openlrLineAttributes>
            </openlrLastLocationReferencePoint>
          </openlrPointAlongLine></openlrPointLocationReference></openlrExtendedPoint></pointExtension>
        </measurementSiteLocation>
      </measurementSiteRecord>
    </root>'''

    site, characteristics = next(parse_measurement_site_table(BytesIO(xml)))

    assert site["equipment_reference"] == "loop-42"
    assert site["computation_method"] == "arithmeticAverageOfSamplesInATimePeriod"
    assert site["carriageway_type"] == "mainCarriageway"
    assert site["tmc_table_version"] == "A"
    assert site["tmc_offset_m"] == 1130
    assert site["openlr_bearing"] == 296
    assert site["openlr_frc"] == "FRC2"
    assert site["openlr_fow"] == "multipleCarriageway"
    assert site["openlr_positive_offset_m"] == 639
    assert len(site["openlr_data"]["lrps"]) == 2
    assert characteristics[0]["accuracy"] == 95.0
    assert characteristics[0]["vehicle_type"] == "anyVehicle"
