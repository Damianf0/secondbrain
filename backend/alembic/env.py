"""
Configuración de Alembic para migraciones.

Lee DATABASE_URL del entorno (no de alembic.ini) para que funcione
tanto en el container Docker como localmente.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importar models para que Alembic los detecte en autogenerate
import app.models  # noqa: F401 — registra todos los models en Base.metadata
from app.db.session import Base

# Configuración del logger desde alembic.ini
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inyectar URL desde el entorno
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Metadata con todos los models registrados
target_metadata = Base.metadata

# Schemas que manejamos con Alembic (excluye pg_catalog, information_schema, etc.)
MANAGED_SCHEMAS = {"core", "media", "processing"}


def include_object(object, name, type_, reflected, compare_to):  # noqa: A002
    """Filtro para que autogenerate solo toque nuestros schemas."""
    if type_ == "table":
        return object.schema in MANAGED_SCHEMAS
    return True


def run_migrations_offline() -> None:
    """Generar SQL sin conectarse a la DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="public",
        include_schemas=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Conectarse a la DB y aplicar migraciones."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="public",
            include_schemas=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
