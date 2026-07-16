"""Quality semantics of the Dutch DATEX II traffic-speed profile."""

from __future__ import annotations

from io import BytesIO

from ndwinfo.parsers.datex_v2 import parse_trafficspeed


def _parse_values(values: str) -> list[dict]:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0"
                    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <payloadPublication xsi:type="MeasuredDataPublication">
        <siteMeasurements>
          <measurementSiteReference id="TEST_SITE" />
          <measurementTimeDefault>2026-07-16T11:14:00Z</measurementTimeDefault>
          {values}
        </siteMeasurements>
      </payloadPublication>
    </d2LogicalModel>
    """
    return list(parse_trafficspeed(BytesIO(xml.encode())))


def _speed_value(index: int, attributes: str, children: str) -> str:
    return f"""
    <measuredValue index="{index}">
      <measuredValue>
        <basicData xsi:type="TrafficSpeed">
          <averageVehicleSpeed {attributes}>{children}</averageVehicleSpeed>
        </basicData>
      </measuredValue>
    </measuredValue>
    """


def test_error_sentinel_is_explicit_and_never_usable():
    rows = _parse_values(
        _speed_value(
            1,
            'numberOfInputValuesUsed="0" supplierCalculatedDataQuality="0"',
            "<dataError>true</dataError><speed>-1</speed>",
        )
        + _speed_value(2, 'numberOfInputValuesUsed="12"', "<speed>-1</speed>")
    )

    for row in rows:
        assert row["measurement_status"] == "error"
        assert row["is_usable"] is False
        assert row["speed_kmh"] is None
    assert rows[0]["data_error"] is True
    assert rows[0]["supplier_quality"] == 0.0
    assert rows[1]["data_error"] is None


def test_no_traffic_is_not_exposed_as_measured_standstill():
    row = _parse_values(
        _speed_value(
            1,
            'numberOfInputValuesUsed="0" numberOfIncompleteInputs="0"',
            "<speed>0</speed>",
        )
    )[0]

    assert row["measurement_status"] == "no_traffic"
    assert row["is_usable"] is False
    assert row["speed_kmh"] is None
    assert row["n_inputs"] == 0
    assert row["n_incomplete_inputs"] == 0


def test_zero_with_observations_is_a_valid_standstill():
    row = _parse_values(
        _speed_value(
            1,
            (
                'numberOfInputValuesUsed="4" numberOfIncompleteInputs="0" '
                'standardDeviation="0" supplierCalculatedDataQuality="92.5" '
                'computationalMethod="arithmeticAverageOfSamplesInATimePeriod"'
            ),
            "<dataError>false</dataError><speed>0</speed>",
        )
    )[0]

    assert row["measurement_status"] == "valid_standstill"
    assert row["is_usable"] is True
    assert row["speed_kmh"] == 0.0
    assert row["data_error"] is False
    assert row["supplier_quality"] == 92.5
    assert row["computational_method"] == "arithmeticAverageOfSamplesInATimePeriod"


def test_positive_speed_remains_a_backward_compatible_measurement():
    row = _parse_values(
        _speed_value(
            1,
            'numberOfInputValuesUsed="8" numberOfIncompleteInputs="1"',
            "<speed>105</speed>",
        )
    )[0]

    assert row["measurement_status"] == "measurement"
    assert row["is_usable"] is True
    assert row["speed_kmh"] == 105.0
    assert row["n_inputs"] == 8
    assert row["n_incomplete_inputs"] == 1
    assert row["raw"]["measurement_status"] == "measurement"


def test_zero_supplier_quality_rejects_even_positive_speed_without_error_flag():
    row = _parse_values(
        _speed_value(
            1,
            'numberOfInputValuesUsed="8" supplierCalculatedDataQuality="0"',
            "<dataError>false</dataError><speed>105</speed>",
        )
    )[0]

    assert row["measurement_status"] == "quality_rejected"
    assert row["is_usable"] is False
    assert row["speed_kmh"] is None


def test_zero_without_no_traffic_signature_fails_open_as_valid_standstill():
    row = _parse_values(_speed_value(1, "", "<speed>0</speed>"))[0]

    assert row["measurement_status"] == "valid_standstill"
    assert row["is_usable"] is True
    assert row["speed_kmh"] == 0.0


def test_flow_quality_fields_are_extracted_and_error_value_is_not_usable():
    row = _parse_values(
        """
        <measuredValue index="1">
          <measuredValue>
            <basicData xsi:type="TrafficFlow">
              <vehicleFlow numberOfInputValuesUsed="0"
                           numberOfIncompleteInputs="0"
                           supplierCalculatedDataQuality="0"
                           computationalMethod="movingAverageOfSamples">
                <dataError>true</dataError>
                <vehicleFlowRate>0</vehicleFlowRate>
              </vehicleFlow>
            </basicData>
          </measuredValue>
        </measuredValue>
        """
    )[0]

    assert row["measurement_status"] == "error"
    assert row["is_usable"] is False
    assert row["flow_veh_h"] is None
    assert row["data_error"] is True
    assert row["n_incomplete_inputs"] == 0
    assert row["supplier_quality"] == 0.0
    assert row["computational_method"] == "movingAverageOfSamples"
