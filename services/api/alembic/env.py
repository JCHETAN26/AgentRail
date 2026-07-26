"""Alembic environment.

The database URL always comes from the validated application settings rather
than from ``alembic.ini``, so migrations cannot be pointed at a different
database than the one the service uses.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models registers them on ``Base.metadata`` for autogenerate.
import agentrail_core.datasets
import agentrail_core.jobs.models  # noqa: F401
from agentrail_api.settings import ApiSettings
from agentrail_core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = ApiSettings()
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
