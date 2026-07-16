"""Feed registry: name → filename, URL suffix, cadence, parser, ingester."""

from typing import Callable, Literal, NotRequired, TypedDict

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
    # realtime: may run while the API is active and has priority on workers.
    # background: only starts while the API is idle.
    # maintenance: only starts after an extended idle period.
    schedule_class: Literal["realtime", "background", "maintenance"]
    # Lower values are scheduled first within a class. This is intentionally
    # independent of list order so priorities remain explicit and testable.
    priority: int


# parser_fn and ingester_cls are filled in as Phase 3/4 work lands.
# The scheduler uses schedule_class + priority; list order is only a stable
# tie-breaker. Heavy feeds never start merely because a user wakes the API.
FEEDS: list[FeedDef] = [
    # --- cadence 60s ---
    {
        "name": "trafficspeed",
        "filename": "trafficspeed.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 0,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "traveltime",
        "filename": "traveltime.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 80,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "actueel_beeld",
        "filename": "actueel_beeld.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 40,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "srti",
        "filename": "veiligheidsgerelateerde_berichten_srti.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 50,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "bridge_openings",
        "filename": "planningsfeed_brugopeningen.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 70,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "closures",
        "filename": "tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 20,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "speed_limits",
        "filename": "tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 30,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "matrix_signs",
        "filename": "Matrixsignaalinformatie.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 10,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "drips",
        "filename": "dynamische_route_informatie_paneel.xml.gz",
        "cadence_s": 60,
        "schedule_class": "realtime",
        "priority": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "charging_geojson",
        "filename": "charging_point_locations.geojson.gz",
        "cadence_s": 60,
        "schedule_class": "background",
        "priority": 30,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "charging_ocpi",
        "filename": "charging_point_locations_ocpi.json.gz",
        "cadence_s": 60,
        "schedule_class": "background",
        "priority": 40,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "truckparking_status",
        "filename": "Truckparking_Parking_Status.xml",
        "cadence_s": 60,
        "schedule_class": "background",
        "priority": 50,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 900s ---
    {
        "name": "roadworks",
        "filename": "planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz",
        "cadence_s": 900,
        "schedule_class": "realtime",
        "priority": 65,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 3600s ---
    {
        "name": "measurement_site",
        "filename": "measurement_current.xml.gz",
        "cadence_s": 3600,
        "schedule_class": "background",
        "priority": 0,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "tariffs_ocpi",
        "filename": "charging_point_tariffs_ocpi.json.gz",
        "cadence_s": 3600,
        "schedule_class": "background",
        "priority": 60,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 86400s ---
    {
        "name": "meetlocaties_shapefile",
        "filename": "ndw_avg_meetlocaties_shapefile.zip",
        "cadence_s": 86400,
        "schedule_class": "maintenance",
        "priority": 30,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "msi_shapefiles",
        "filename": "ndw_msi_shapefiles_latest.zip",
        "cadence_s": 86400,
        "schedule_class": "background",
        "priority": 10,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "emission_zones",
        "filename": "emissiezones.xml.gz",
        "cadence_s": 86400,
        "schedule_class": "background",
        "priority": 70,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "truckparking_table",
        "filename": "Truckparking_Parking_Table.xml",
        "cadence_s": 86400,
        "schedule_class": "background",
        "priority": 55,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "verkeersborden_csv",
        "filename": "verkeersborden_actueel_beeld.csv.gz",
        "cadence_s": 86400,
        "schedule_class": "maintenance",
        "priority": 90,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 604800s ---
    {
        "name": "vild_shapefile",
        "filename": "VILD6.13.A.zip",
        "cadence_s": 604800,
        "schedule_class": "maintenance",
        "priority": 40,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- cadence 86400s, non-NDW source ---
    {
        "name": "nwb_wegvakken",
        "filename": "Wegvakken.gpkg",
        "cadence_s": 86400,
        "schedule_class": "maintenance",
        "priority": 60,
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
        "schedule_class": "maintenance",
        "priority": 70,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        # Explicit OSM bootstrap input. There is intentionally no poller
        # ingester: a graph build is a staged operational action and is never
        # triggered by an API request or the 10-second poll loop.
        "name": "osm_pbf",
        "filename": "netherlands-latest.osm.pbf",
        "cadence_s": 86400,
        "schedule_class": "maintenance",
        "priority": 100,
        "parser_fn": None,
        "ingester_cls": None,
        "url": settings.osm_pbf_url,
    },
]

FEEDS_BY_NAME: dict[str, FeedDef] = {f["name"]: f for f in FEEDS}
