"""Runtime configuration, all from environment variables (spec section 8)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent

DATABASE_PATH = os.environ.get("DATABASE_PATH", str(REPO_DIR / "data" / "mogi.db"))
PORT = int(os.environ.get("PORT", "8000"))
TZ_NAME = os.environ.get("TZ", "UTC")

# Every date in Lounge.pdf is bare (`5/26`, `7/30`) with no year. The doc runs
# 5/26 -> 7/30 and contains the MKWorld 1-year anniversary tournament, which
# dates the whole archive to 2026.
IMPORT_DEFAULT_YEAR = int(os.environ.get("IMPORT_DEFAULT_YEAR", "2026"))

BACKUP_DIR = os.environ.get("BACKUP_DIR", str(Path(DATABASE_PATH).parent / "backups"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "30"))
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "4"))
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "1") not in ("0", "false", "False")

# MKCentral's public Lounge API defaults to the current season when `season` is
# omitted, so season changes do not require a deployment or config edit.
LOUNGE_BASE_URL = os.environ.get("LOUNGE_BASE_URL", "https://lounge.mkcentral.com")
LOUNGE_GAME = os.environ.get("LOUNGE_GAME", "mkworld12p")
LOUNGE_HTTP_TIMEOUT = float(os.environ.get("LOUNGE_HTTP_TIMEOUT", "10"))
LOUNGE_PLAYER_ID = int(os.environ.get("LOUNGE_PLAYER_ID", "67656"))
LOUNGE_MMR_REFRESH_HOURS = max(
    1, int(os.environ.get("LOUNGE_MMR_REFRESH_HOURS", "24"))
)
LOUNGE_NAME_REFRESH_DAYS = max(1, int(os.environ.get("LOUNGE_NAME_REFRESH_DAYS", "7")))
LOUNGE_REFRESH_HOUR = int(os.environ.get("LOUNGE_REFRESH_HOUR", "5"))
LOUNGE_REFRESH_ENABLED = os.environ.get("LOUNGE_REFRESH_ENABLED", "1") not in (
    "0", "false", "False",
)


def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(TZ_NAME)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def to_local(dt: datetime) -> datetime:
    """UTC-stored timestamp -> configured display timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz())


def to_utc(dt: datetime) -> datetime:
    """Naive local wall-clock -> naive UTC, for storage."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def round_to_hour(dt: datetime) -> datetime:
    """Round a wall-clock datetime to its nearest whole hour (halves round up)."""
    return (dt + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
