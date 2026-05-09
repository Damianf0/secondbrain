"""
Configuración de Alembic para migraciones.

Lee DATABASE_URL del entorno (no de alembic.ini) para que funcione
tanto en el container Docker como localmente.

En Sprint 0 está vacío. Cuando agreguemos models en Sprint 1,
acá registramos `target_metadata`.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Configuración del logger desde alembic.ini
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inyectar URL desde el entorno
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# En Sprint 1+ acá importamos los models y ponemos:
#   from app.db.models import Base
#   target_metadata = Base.metadata
target_metadata = None


def run_migrations_offline() -> None:
    """Generar SQL sin conectarse a la DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="public",
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
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
