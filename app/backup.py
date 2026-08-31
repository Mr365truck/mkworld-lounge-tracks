"""Scheduled maintenance: nightly backups and Lounge data refresh checks.

`VACUUM INTO` a timestamped file in /data/backups, keep the newest 30. ZFS snapshots
on the dataset are the real backup; this is the belt to that pair of braces, because a
snapshot of a live SQLite file is *usually* recoverable given WAL while a checkpointed
copy is unambiguously safe.

This runs **in-process**. There is no SSH to the NAS, so a host cron is not available.
"""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config, db

log = logging.getLogger("mogi.backup")


def run_backup(keep: int | None = None) -> str | None:
    keep = config.BACKUP_KEEP if keep is None else keep
    outdir = Path(config.BACKUP_DIR)
    outdir.mkdir(parents=True, exist_ok=True)
    # Microseconds prevent a quick restart from colliding with the startup backup.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = outdir / f"mogi-{stamp}.db"

    with db.connect() as conn:
        # VACUUM INTO refuses to overwrite, which is the behaviour we want.
        conn.exec_driver_sql("VACUUM INTO :p", {"p": str(target)})
    log.info("wrote backup %s (%d bytes)", target, target.stat().st_size)

    existing = sorted(outdir.glob("mogi-*.db"))
    for old in existing[:-keep] if keep > 0 else []:
        try:
            os.remove(old)
            log.info("pruned old backup %s", old)
        except OSError as e:
            log.warning("could not prune %s: %s", old, e)
    return str(target)


def start_scheduler() -> BackgroundScheduler | None:
    if not config.BACKUP_ENABLED and not config.LOUNGE_REFRESH_ENABLED:
        log.info("scheduled maintenance disabled")
        return None
    sched = BackgroundScheduler(timezone=str(config.local_tz()))
    if config.BACKUP_ENABLED:
        sched.add_job(
            run_backup,
            CronTrigger(hour=config.BACKUP_HOUR, minute=0),
            id="nightly-vacuum-into",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        log.info("nightly backup scheduled for %02d:00 %s",
                 config.BACKUP_HOUR, config.TZ_NAME)
    else:
        log.info("backups disabled (BACKUP_ENABLED=0)")
    if config.LOUNGE_REFRESH_ENABLED:
        from .current_mmr import next_refresh_at, refresh as refresh_current_mmr
        sched.add_job(
            refresh_current_mmr,
            IntervalTrigger(hours=config.LOUNGE_MMR_REFRESH_HOURS),
            id="current-lounge-mmr-refresh",
            replace_existing=True,
            next_run_time=next_refresh_at(),
            misfire_grace_time=3600,
        )
        log.info("current Lounge MMR refresh scheduled every %d hours",
                 config.LOUNGE_MMR_REFRESH_HOURS)

        # This check runs daily, but each row is fetched only once it is a week old.
        # Persisting last_refreshed_at means container restarts cannot reset the clock.
        from .do_not_mogi import refresh_names
        sched.add_job(
            refresh_names,
            CronTrigger(hour=config.LOUNGE_REFRESH_HOUR, minute=15),
            id="do-not-mogi-name-refresh",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        log.info("Lounge name refresh check scheduled for %02d:15 %s",
                 config.LOUNGE_REFRESH_HOUR, config.TZ_NAME)
    sched.start()
    return sched
