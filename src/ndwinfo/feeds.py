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
    # "realtime" always runs when due; "background" waits for API idle;
    # "maintenance" (large/slow imports) waits for a longer idle window and
    # shares a capped concurrency budget so it can't starve realtime feeds.
    # See poller.py's scheduling. Defaults to "background" when omitted.
    schedule_class: NotRequired[str]
    # Only set for sources fetched via a form-encoded POST instead of NDW's
    # usual conditional-GET (see download.py's _fetch_post). form_data/
    # extra_headers are ignored on the default GET path.
    method: NotRequired[str]
    form_data: NotRequired[dict[str, str]]
    extra_headers: NotRequired[dict[str, str]]


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
        "schedule_class": "realtime",
    },
    {
        "name": "traveltime",
        "filename": "traveltime.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "actueel_beeld",
        "filename": "actueel_beeld.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "srti",
        "filename": "veiligheidsgerelateerde_berichten_srti.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "bridge_openings",
        "filename": "planningsfeed_brugopeningen.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "closures",
        "filename": "tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "speed_limits",
        "filename": "tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "matrix_signs",
        "filename": "Matrixsignaalinformatie.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "drips",
        "filename": "dynamische_route_informatie_paneel.xml.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "charging_geojson",
        "filename": "charging_point_locations.geojson.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "charging_ocpi",
        "filename": "charging_point_locations_ocpi.json.gz",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        "name": "truckparking_status",
        "filename": "Truckparking_Parking_Status.xml",
        "cadence_s": 60,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    # --- cadence 900s ---
    {
        "name": "roadworks",
        "filename": "planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz",
        "cadence_s": 900,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
    # --- cadence 3600s ---
    {
        "name": "measurement_site",
        "filename": "measurement_current.xml.gz",
        "cadence_s": 3600,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
    {
        "name": "tariffs_ocpi",
        "filename": "charging_point_tariffs_ocpi.json.gz",
        "cadence_s": 3600,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
    # --- cadence 86400s ---
    {
        # ~72MB shapefile zip.
        "name": "meetlocaties_shapefile",
        "filename": "ndw_avg_meetlocaties_shapefile.zip",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "maintenance",
    },
    {
        "name": "msi_shapefiles",
        "filename": "ndw_msi_shapefiles_latest.zip",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
    {
        "name": "emission_zones",
        "filename": "emissiezones.xml.gz",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
    {
        "name": "truckparking_table",
        "filename": "Truckparking_Parking_Table.xml",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
    {
        # >200M decompressed CSV — the heaviest recurring import.
        "name": "verkeersborden_csv",
        "filename": "verkeersborden_actueel_beeld.csv.gz",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "maintenance",
    },
    # --- cadence 604800s ---
    {
        # ~40MB shapefile zip.
        "name": "vild_shapefile",
        "filename": "VILD6.13.A.zip",
        "cadence_s": 604800,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "maintenance",
    },
    {
        # Geofabrik country extract, not an NDW file. Driving-road ways only
        # (motorway/trunk/primary/secondary + _link) -- see docs/11-osm-pbf.md.
        "name": "osm_netherlands",
        "filename": "netherlands-latest.osm.pbf",
        "cadence_s": 604800,
        "parser_fn": None,
        "ingester_cls": None,
        "url": settings.osm_netherlands_url,
        "schedule_class": "maintenance",
    },
    {
        # ANWB jams/roadworks/dynamic radars, not an NDW file — see
        # docs/plans/anwb-incidents-plan.md.
        "name": "anwb_incidents",
        "filename": "anwb_incidents.json",
        "url": "https://api.anwb.nl/routing/v1/incidents/incidents-desktop",
        "cadence_s": 300,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "realtime",
    },
    {
        # Flitspalen.nl static/fixed speed cameras (NL subset only), not an
        # NDW file — see docs/plans/anwb-incidents-plan.md.
        "name": "flitspalen_cameras",
        "filename": "flitspalen_cameras.json",
        "url": "https://www.flitspalen.nl/karte/",
        "method": "POST",
        "form_data": {
            "xhr": "1", "action": "all",
            "latMax": "53.7", "lngMax": "7.2", "latMin": "50.7", "lngMin": "3.2",
        },
        "extra_headers": {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.flitspalen.nl",
            "Referer": "https://www.flitspalen.nl/karte/",
            "Cookie": "LAN=nl",
        },
        "cadence_s": 604800,
        "parser_fn": None,
        "ingester_cls": None,
        "schedule_class": "background",
    },
]

FEEDS_BY_NAME: dict[str, FeedDef] = {f["name"]: f for f in FEEDS}
