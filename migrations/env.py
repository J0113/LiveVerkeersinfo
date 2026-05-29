import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ndwinfo.db import Base  # noqa: E402
import ndwinfo.models  # noqa: E402, F401 — registers all ORM classes on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables that belong to PostGIS/Tiger/Topology extensions — never drop these.
_EXTENSION_TABLES = {
    "spatial_ref_sys", "topology", "layer",
    "tabblock", "tabblock20", "addrfeat", "addr", "bg", "cousub", "county",
    "edges", "faces", "featnames", "geocode_settings", "geocode_settings_default",
    "loader_lookuptables", "loader_platform", "loader_variables", "pagc_gaz",
    "pagc_lex", "pagc_rules", "place", "state", "tract", "zcta5", "zip_lookup",
    "zip_lookup_all", "zip_lookup_base", "zip_state", "zip_state_loc",
    "county_lookup", "countysub_lookup", "direction_lookup", "place_lookup",
    "secondary_unit_lookup", "state_lookup", "street_type_lookup",
}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in _EXTENSION_TABLES:
        return False
    return True


def get_url() -> str:
    from ndwinfo.config import settings
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
