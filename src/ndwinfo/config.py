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
    # Two workers keep the large live feeds moving without tripling peak parser,
    # database, and decompression memory on small local deployments.
    poller_max_workers: int = 2
    db_pool_size: int = 4
    db_max_overflow: int = 2
    db_pool_recycle_s: int = 1800
    # comma-separated names to skip, e.g. "verkeersborden_csv,msi_shapefiles"
    disabled_feeds: str = ""
    nwb_wegvakken_url: str = (
        "https://downloads.rijkswaterstaatdata.nl/nwb-wegen/geogegevens/"
        "geopackage/NWB-dagelijks/Wegvakken/Wegvakken.gpkg"
    )
    nwb_max_features: int = 5000  # per-viewport row cap for /api/nwb/roads
    nwb_diagnostic_mode: bool = False


settings = Settings()
