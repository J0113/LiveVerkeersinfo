"""Feed registry: name → filename, URL suffix, cadence, parser, ingester."""

from typing import Callable, NotRequired, TypedDict

from ndwinfo.config import settings


class FeedDef(TypedDict, total=False):
    name: str
    filename: str
    cadence_s: int
    parser_fn: Callable | None
    ingester_cls: type | None
    url: NotRequired[str]  # absolute URL override; bypasses ndw_base_url join
    # Most feeds are relative to NDW_BASE_URL. Versioned external datasets can
    # instead expose an Apache-style index resolved by the downloader.
    index_url: NotRequired[str]


# parser_fn and ingester_cls are filled in as Phase 3/4 work lands.
# Ordered fastest cadence -> slowest: smaller/faster files poll first each tick.
FEEDS: list[FeedDef] = [
    # --- cadence 60s ---
    {
        "name": "trafficspeed",
        "filename": "trafficspeed.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "traveltime",
        "filename": "traveltime.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "actueel_beeld",
        "filename": "actueel_beeld.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "srti",
        "filename": "veiligheidsgerelateerde_berichten_srti.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "bridge_openings",
        "filename": "planningsfeed_brugopeningen.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "closures",
        "filename": "tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "speed_limits",
        "filename": "tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "matrix_signs",
        "filename": "Matrixsignaalinformatie.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "drips",
        "filename": "dynamische_route_informatie_paneel.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "charging_geojson",
        "filename": "charging_point_locations.geojson.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "charging_ocpi",
        "filename": "charging_point_locations_ocpi.json.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "truckparking_status",
        "filename": "Truckparking_Parking_Status.xml",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 900s ---
    {
        "name": "roadworks",
        "filename": "planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz",
        "cadence_s": 900,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 3600s ---
    {
        "name": "measurement_site",
        "filename": "measurement_current.xml.gz",
        "cadence_s": 3600,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "tariffs_ocpi",
        "filename": "charging_point_tariffs_ocpi.json.gz",
        "cadence_s": 3600,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 86400s ---
    {
        "name": "meetlocaties_shapefile",
        "filename": "ndw_avg_meetlocaties_shapefile.zip",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "msi_shapefiles",
        "filename": "ndw_msi_shapefiles_latest.zip",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "emission_zones",
        "filename": "emissiezones.xml.gz",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "truckparking_table",
        "filename": "Truckparking_Parking_Table.xml",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "verkeersborden_csv",
        "filename": "verkeersborden_actueel_beeld.csv.gz",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 604800s ---
    {
        "name": "vild_shapefile",
        "filename": "VILD6.13.A.zip",
        "cadence_s": 604800,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 86400s, non-NDW source ---
    {
        "name": "nwb_wegvakken",
        "filename": "Wegvakken.gpkg",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "url": settings.nwb_wegvakken_url,
    },
    {
        # WEGGEG publishes one versioned package per month rather than a stable
        # "latest" filename. `index_url` is resolved to the newest DD-MM-YYYY
        # package by download.fetch, while this local filename stays stable.
        "name": "weggeg_rijstroken",
        "filename": "weggeg_rijstroken.zip",
        "index_url": "https://downloads.rijkswaterstaatdata.nl/weggeg/geogegevens/"
        "shapefile/weggeg_kenmerkniveau/",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        # Geofabrik province extract, not an NDW file. Driving-road ways only
        # (motorway/trunk/primary/secondary + _link) -- see docs/11-osm-pbf.md.
        "name": "osm_noord_holland",
        "filename": "noord-holland-latest.osm.pbf",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "url": settings.osm_noord_holland_url,
    },
]

FEEDS_BY_NAME: dict[str, FeedDef] = {f["name"]: f for f in FEEDS}
