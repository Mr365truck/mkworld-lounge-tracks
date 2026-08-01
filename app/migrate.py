"""Run Alembic migrations programmatically.

Called at startup so the container is self-migrating: there is no SSH to the NAS to
run `alembic upgrade head` by hand.
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from . import config

log = logging.getLogger("mogi.migrate")


def alembic_config() -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return cfg


def upgrade_to_head() -> None:
    log.info("migrating %s to head", config.DATABASE_PATH)
    command.upgrade(alembic_config(), "head")


def current_revision() -> str | None:
    from alembic.runtime.migration import MigrationContext
    from .db import engine
    with engine().connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()
