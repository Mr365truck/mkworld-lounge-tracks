"""mogi-tracker — FastAPI application.

No auth (spec section 8): a single user on a trusted LAN, which means anything on that
network can read and write the database. That is the accepted posture at this scale.
The routes are grouped so an auth middleware can be added later without touching them.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import backup, config, db, migrate
from .routes import api, exports, pages
from .templating import templates

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mogi")

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    migrate.upgrade_to_head()
    # Seeding is idempotent and also runs in migration 0002; repeating it here means
    # a track added to Appendix A lands on the next restart without a new revision.
    from .seed import seed_tracks
    with db.connect() as conn:
        result = seed_tracks(conn)
    if result["tracks_added"] or result["aliases_added"]:
        log.info("seed: %s", result)
    _scheduler = backup.start_scheduler()
    log.info("ready on port %s, TZ=%s, db=%s",
             config.PORT, config.TZ_NAME, config.DATABASE_PATH)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="mogi-tracker", lifespan=lifespan, docs_url="/api/docs",
              openapi_url="/api/openapi.json")

app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(exports.router)


@app.get("/healthz")
def healthz():
    try:
        with db.read() as conn:
            conn.exec_driver_sql("SELECT 1")
        return {"status": "ok", "revision": migrate.current_revision(),
                "db_bytes": db.db_size_bytes()}
    except Exception as e:  # pragma: no cover — health check must not raise
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


@app.exception_handler(404)
async def not_found(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return templates.TemplateResponse(
        request, "404.html", {"path": request.url.path}, status_code=404)
