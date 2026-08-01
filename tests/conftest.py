import os
import tempfile

import pytest

# Point the app at a throwaway database before anything imports app.config.
_TMP = tempfile.mkdtemp(prefix="mogi-test-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test.db")
os.environ["TZ"] = "UTC"
os.environ["BACKUP_ENABLED"] = "0"

from app import db  # noqa: E402
from app.schema import metadata  # noqa: E402
from app.seed import seed_tracks  # noqa: E402


@pytest.fixture
def engine(tmp_path):
    eng = db.make_engine(str(tmp_path / "t.db"))
    metadata.create_all(eng)
    db.set_engine(eng)
    yield eng
    eng.dispose()
    db.set_engine(None)


@pytest.fixture
def conn(engine):
    with engine.begin() as c:
        seed_tracks(c)
    with engine.begin() as c:
        yield c


@pytest.fixture
def client(engine):
    """TestClient over the same throwaway engine, with the schema already made."""
    from fastapi.testclient import TestClient

    from app.main import app

    with engine.begin() as c:
        seed_tracks(c)

    # The real lifespan runs Alembic against DATABASE_PATH; the fixture engine is
    # already migrated via create_all, so skip it and keep the engine we set.
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as c:
        yield c


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _noop_lifespan(app):
    yield
