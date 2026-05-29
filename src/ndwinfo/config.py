from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ndwinfo:ndwinfo@localhost:5432/ndwinfo"
    ndw_base_url: str = "https://opendata.ndw.nu"
    data_dir: str = "./data"
    max_bbox_area: float = 25.0
    api_default_limit: int = 500
    api_max_limit: int = 2000


settings = Settings()
