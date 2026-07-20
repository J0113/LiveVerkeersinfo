"""Ingester registry: feed_name → ingester instance."""

from ndwinfo.ingest.charging import ChargingGeojsonIngester, TariffIngester
from ndwinfo.ingest.emission import EmissionZoneIngester
from ndwinfo.ingest.measurement import (
    MeasurementSiteIngester,
    TrafficspeedIngester,
    TraveltimeIngester,
)
from ndwinfo.ingest.osm_roads import OsmRoadIngester
from ndwinfo.ingest.parking import TruckParkingStatusIngester, TruckParkingTableIngester
from ndwinfo.ingest.reference import (
    MeetlocatiesIngester,
    MsiShapefileIngester,
    VildIngester,
)
from ndwinfo.ingest.signs import DripIngester, MatrixSignIngester
from ndwinfo.ingest.situations import (
    ActueleBeeldIngester,
    BridgeOpeningsIngester,
    ClosuresIngester,
    RoadworksIngester,
    SpeedLimitsIngester,
    SrtiIngester,
)
from ndwinfo.ingest.verkeersborden import TrafficSignIngester

INGESTERS: dict[str, object] = {
    "measurement_site": MeasurementSiteIngester(),
    "meetlocaties_shapefile": MeetlocatiesIngester(),
    "vild_shapefile": VildIngester(),
    "osm_netherlands": OsmRoadIngester(feed_name="osm_netherlands", extract_key="netherlands"),
    "trafficspeed": TrafficspeedIngester(),
    "traveltime": TraveltimeIngester(),
    "actueel_beeld": ActueleBeeldIngester,
    "srti": SrtiIngester,
    "roadworks": RoadworksIngester,
    "bridge_openings": BridgeOpeningsIngester,
    "closures": ClosuresIngester,
    "speed_limits": SpeedLimitsIngester,
    "matrix_signs": MatrixSignIngester(),
    "drips": DripIngester(),
    "msi_shapefiles": MsiShapefileIngester(),
    "emission_zones": EmissionZoneIngester(),
    "charging_geojson": ChargingGeojsonIngester(),
    "tariffs_ocpi": TariffIngester(),
    "truckparking_table": TruckParkingTableIngester(),
    "truckparking_status": TruckParkingStatusIngester(),
    "verkeersborden_csv": TrafficSignIngester(),
}
