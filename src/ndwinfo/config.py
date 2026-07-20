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
    poller_max_workers: int = 8  # run due feeds concurrently, up to this many at once
    # Maintenance-class feeds (large daily/weekly imports) wait for a longer
    # idle window than background feeds before they're allowed to start.
    poller_maintenance_idle_s: int = 900
    # Cap concurrent non-realtime (background/maintenance) submissions so a
    # burst of due bulk feeds can never fill every worker and starve realtime.
    poller_bulk_max_inflight: int = 4
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_recycle_s: int = 1800
    # comma-separated names to skip, e.g. "verkeersborden_csv,msi_shapefiles"
    disabled_feeds: str = ""
    osm_netherlands_url: str = (
        "https://download.geofabrik.de/europe/netherlands-latest.osm.pbf"
    )
    osm_max_features: int = 5000  # per-viewport row cap for /api/osm/roads
    osm_lane_max_features: int = 8000  # per-viewport row cap for /api/osm/lanes


settings = Settings()
