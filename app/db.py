"""Engine and connection helpers.

SQLite with WAL and synchronous=NORMAL (spec section 2) -- safe on ZFS, and the
single-file DB means ZFS snapshots are the backup strategy.
"""
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from . import config

_engine: Engine | None = None


def _configure(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def make_engine(path: str | None = None) -> Engine:
    path = path or config.DATABASE_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    event.listen(eng, "connect", _configure)
    return eng


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def set_engine(eng: Engine) -> None:
    """Used by tests to point the app at a throwaway database."""
    global _engine
    _engine = eng


@contextmanager
def connect():
    """Transactional connection. Commits on clean exit, rolls back on exception."""
    with engine().begin() as conn:
        yield conn


@contextmanager
def read():
    with engine().connect() as conn:
        yield conn


def db_size_bytes() -> int:
    try:
        return os.path.getsize(config.DATABASE_PATH)
    except OSError:
        return 0
