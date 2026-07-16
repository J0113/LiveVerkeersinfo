from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ndwinfo:ndwinfo@localhost:5432/ndwinfo"
    ndw_base_url: str = "https://opendata.ndw.nu"
    data_dir: str = "./data"
    max_bbox_area: float = 25.0
    api_default_limit: int = 500
    api_max_limit: int = 2000
    poller_idle_timeout_s: int = 300
    # Keep this deliberately small: feed ingesters are generally I/O- and
    # database-heavy, so more workers mostly increase contention. At most one
    # worker serves bulk work while idle; live work keeps priority.
    poller_max_workers: int = 3
    poller_bulk_max_inflight: int = 1
    # Very large static/reference imports only start after a longer idle period.
    # Smaller background refreshes use poller_idle_timeout_s.
    poller_maintenance_idle_s: int = 900
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_recycle_s: int = 1800
    # comma-separated names to skip, e.g. "verkeersborden_csv,msi_shapefiles"
    disabled_feeds: str = ""
    nwb_wegvakken_url: str = (
        "https://downloads.rijkswaterstaatdata.nl/nwb-wegen/geogegevens/"
        "geopackage/NWB-dagelijks/Wegvakken/Wegvakken.gpkg"
    )
    nwb_max_features: int = 5000  # per-viewport row cap for /api/nwb/roads
    nwb_diagnostic_mode: bool = False
    # Bounded proof-of-concept only. Production OSM would use a local PBF and
    # replication pipeline instead of a public Overpass instance.
    osm_overpass_url: str = "https://overpass-api.de/api/interpreter"
    osm_overpass_fallback_url: str = "https://overpass.private.coffee/api/interpreter"
    osm_poc_cache_ttl_s: int = 1800
    osm_poc_max_features: int = 12000
    # Production/MVP OSM graph input. This path is read by the explicit import
    # command; it is never queried or downloaded in an API request.
    osm_pbf_url: str = (
        "https://download.geofabrik.de/europe/netherlands-latest.osm.pbf"
    )
    osm_pbf_path: str = "./data/netherlands-latest.osm.pbf"
    osm_import_batch_size: int = 5000
    osm_import_temp_dir: str = ""
    osm_location_index: str = "sparse_file_array"
    osm_highway_classes: str = (
        "motorway,motorway_link,trunk,trunk_link,primary,primary_link,"
        "secondary,secondary_link,tertiary,tertiary_link"
    )
    # Production OSM graph, source binding and driving-corridor bounds.
    road_api_max_features: int = 2000
    # NDW observation timestamps can trail wall-clock time by several minutes;
    # keep the timestamp visible and fail closed after ten minutes.
    road_speed_stale_after_s: int = 600
    # Expand coverage only across complete one-to-one directed chains.
    road_speed_propagation_max_m: float = 1500.0
    road_speed_interpolation_max_m: float = 5000.0
    road_matrix_stale_after_s: int = 180
    road_drip_stale_after_s: int = 180
    road_corridor_max_radius_m: float = 200.0
    road_corridor_max_lookahead_m: float = 5000.0
    # Connected-path traversal is deliberately smaller than the feature cap.
    # Each endpoint lookup is index-backed and bounded by the branch limit.
    road_topology_max_ahead_m: float = 5000.0
    road_topology_max_behind_m: float = 500.0
    road_topology_max_edges: int = 128
    road_topology_max_branches: int = 8
    source_binding_max_distance_m: float = 80.0
    source_binding_max_heading_delta_deg: float = 85.0
    # VILD is only a direction fallback when its referenced line is physically
    # close to the measurement site. This prevents sparse/incomplete VILD
    # coverage from assigning a remote line's direction.
    source_binding_vild_max_distance_m: float = 50.0
    source_binding_min_confidence: float = 0.5
    source_binding_min_margin: float = 4.0
    # Eight covers both directions plus nearby parallel/main carriageways while
    # keeping a national background rebuild bounded.
    source_binding_max_candidates: int = 8


settings = Settings()
