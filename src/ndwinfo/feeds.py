"""Feed registry: name → filename, URL suffix, cadence, parser, ingester."""

from typing import Callable, TypedDict


class FeedDef(TypedDict):
    name: str
    filename: str
    cadence_s: int
    parser_fn: Callable | None
    ingester_cls: type | None


# parser_fn and ingester_cls are filled in as Phase 3/4 work lands.
FEEDS: list[FeedDef] = [
    # --- Reference / static ---
    {
        "name": "measurement_site",
        "filename": "measurement_current.xml.gz",
        "cadence_s": 3600,
        "parser_fn": None,
        "ingester_cls": None,
    },
    {
        "name": "meetlocaties_shapefile",
        "filename": "ndw_avg_meetlocaties_shapefile.zip",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- Real-time measurement ---
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
    # --- Situations ---
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
        "name": "roadworks",
        "filename": "planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz",
        "cadence_s": 900,
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
    # --- Signs & VMS ---
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
        "name": "msi_shapefiles",
        "filename": "ndw_msi_shapefiles_latest.zip",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- Emission zones ---
    {
        "name": "emission_zones",
        "filename": "emissiezones.xml.gz",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- EV charging ---
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
        "name": "tariffs_ocpi",
        "filename": "charging_point_tariffs_ocpi.json.gz",
        "cadence_s": 3600,
        "parser_fn": None,
        "ingester_cls": None,
    },
    # --- Truck parking ---
    {
        "name": "truckparking_table",
        "filename": "Truckparking_Parking_Table.xml",
        "cadence_s": 86400,
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
    # --- Traffic signs ---
    {
        "name": "verkeersborden_csv",
        "filename": "verkeersborden_actueel_beeld.csv.gz",
        "cadence_s": 86400,
        "parser_fn": None,
        "ingester_cls": None,
    },
]

FEEDS_BY_NAME: dict[str, FeedDef] = {f["name"]: f for f in FEEDS}
