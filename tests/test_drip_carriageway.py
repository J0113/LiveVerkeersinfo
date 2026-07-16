from io import BytesIO
from types import SimpleNamespace

from ndwinfo.api.routers.signs import _drip_properties
from ndwinfo.parsers.datex_v3 import parse_drip


DRIP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<mc:messageContainer
    xmlns:mc="http://datex2.eu/schema/3/messageContainer"
    xmlns:vms="http://datex2.eu/schema/3/vms"
    xmlns:loc="http://datex2.eu/schema/3/locationReferencing"
    xmlns:com="http://datex2.eu/schema/3/common">
  <vms:vmsControllerStatus>
    <vms:vmsControllerReference id="controller-1"/>
    <vms:vmsStatus vmsIndex="1">
      <vms:vmsStatus>
        <vms:workingStatus>working</vms:workingStatus>
        <vms:statusUpdateTime>2026-07-16T12:00:00Z</vms:statusUpdateTime>
        <vms:vmsMessage><vms:vmsMessage>
          <vms:image><vms:imageData>YWJj</vms:imageData><vms:imageFormat>png</vms:imageFormat></vms:image>
        </vms:vmsMessage></vms:vmsMessage>
      </vms:vmsStatus>
    </vms:vmsStatus>
  </vms:vmsControllerStatus>
  <vms:vmsController id="controller-1">
    <vms:vms vmsIndex="1"><vms:vms>
      <vms:description><com:values><com:value lang="nl">DRIP test</com:value></com:values></vms:description>
      <vms:vmsType>colourGraphic</vms:vmsType>
      <vms:vmsLocation>
        <loc:supplementaryPositionalDescription>
          <loc:carriageway><loc:carriageway>mainCarriageway</loc:carriageway></loc:carriageway>
        </loc:supplementaryPositionalDescription>
        <loc:pointByCoordinates>
          <loc:bearing>191</loc:bearing>
          <loc:pointCoordinates><loc:latitude>52.1</loc:latitude><loc:longitude>4.3</loc:longitude></loc:pointCoordinates>
        </loc:pointByCoordinates>
      </vms:vmsLocation>
    </vms:vms></vms:vms>
  </vms:vmsController>
</mc:messageContainer>"""


def test_parse_drip_preserves_source_carriageway():
    rows = list(parse_drip(BytesIO(DRIP_XML)))

    assert len(rows) == 1
    assert rows[0]["carriageway"] == "mainCarriageway"
    assert rows[0]["bearing"] == 191
    assert rows[0]["raw"]["carriageway"] == "mainCarriageway"


def test_drip_api_keeps_images_opt_in_and_exposes_carriageway():
    row = SimpleNamespace(
        controller_id="controller-1",
        vms_index=1,
        description="DRIP test",
        vms_type="colourGraphic",
        physical_support="roadsideMounted",
        bearing=191,
        carriageway="mainCarriageway",
        num_display_areas=1,
        display_text=None,
        message={
            "working_status": "working",
            "status_update_time": "2026-07-16T12:00:00Z",
            "image_format": "png",
            "image_data": "YWJj",
        },
    )

    compact = _drip_properties(row)
    graphic = _drip_properties(row, include_image=True)

    assert compact["carriageway"] == "mainCarriageway"
    assert compact["has_image"] is True
    assert "image_b64" not in compact
    assert "image_format" not in compact
    assert graphic["image_b64"] == "YWJj"
    assert graphic["image_format"] == "png"
