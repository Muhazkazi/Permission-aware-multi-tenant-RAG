"""Alembic migration environment configuration."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.db.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Importing app.db.models registers every model with Base.metadata.
target_metadata = Base.metadata


def get_database_url() -> str:
    """Read the database URL from application settings when Alembic runs."""

    database_url = get_settings().database_url
    if not database_url:
        message = "DATABASE_URL must be configured before running migrations."
        raise RuntimeError(message)
    return database_url


def run_migrations_offline() -> None:
    """Generate SQL without opening a database connection."""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a connection created only for this command."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
