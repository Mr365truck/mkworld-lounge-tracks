"""Alembic environment.

The database URL comes from the app config (DATABASE_PATH), not alembic.ini, so the
container and the tests agree without a second source of truth.
"""
from logging.config import fileConfig

from alembic import context

from app.db import make_engine
from app.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _engine():
    # `alembic -x db=/path/to.db upgrade head` overrides, for one-off restores.
    override = context.get_x_argument(as_dictionary=True).get("db")
    return make_engine(override) if override else make_engine()


def run_migrations_offline() -> None:
    from app import config as appconfig
    context.configure(
        url=f"sqlite+pysqlite:///{appconfig.DATABASE_PATH}",
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = _engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the
            # table instead, which matters because every CHECK constraint here
            # would otherwise be unalterable.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
