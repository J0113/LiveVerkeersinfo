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
    # comma-separated names to skip, e.g. "verkeersborden_csv,msi_shapefiles"
    disabled_feeds: str = ""
    nwb_pdok_url: str = (
        "https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1/"
        "collections/wegvakken/items"
    )
    nwb_request_timeout_s: float = 20.0
    nwb_cache_ttl_s: int = 3600
    nwb_cache_max_entries: int = 128
    nwb_max_features: int = 5000
    nwb_diagnostic_mode: bool = False


settings = Settings()
