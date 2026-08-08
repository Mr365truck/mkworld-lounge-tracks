"""Database durability tests.

These tests intentionally focus on preserving already-recorded data. Feature tests
belong elsewhere; a failure here means a release could lose or corrupt the database.
"""
import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app import backup, config, db
from app.queries import create_session
from app.schema import import_issues, races, sessions, tracks
from app.seed import seed_tracks


def test_connections_enable_strong_durability_and_foreign_keys(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 2  # FULL
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_failed_transaction_rolls_back_every_write(engine):
    """A later SQL error must not leave earlier writes half-committed."""
    with engine.begin() as conn:
        seed_tracks(conn)
        session_id = create_session(conn)
        conn.execute(update(races).where(
            (races.c.session_id == session_id) & (races.c.race_num == 1)
        ).values(placement=7))

    with pytest.raises(IntegrityError):
        with db.connect() as conn:
            conn.execute(update(races).where(
                (races.c.session_id == session_id) & (races.c.race_num == 1)
            ).values(placement=2))
            # Duplicate race_num violates the unique constraint after the update.
            conn.execute(insert(races).values(session_id=session_id, race_num=1))

    with engine.connect() as conn:
        placement = conn.execute(select(races.c.placement).where(
            (races.c.session_id == session_id) & (races.c.race_num == 1)
        )).scalar_one()
    assert placement == 7


def test_foreign_keys_protect_tracks_and_cascade_session_children(engine):
    """Referenced tracks cannot disappear; deleting a session leaves no orphans."""
    with engine.begin() as conn:
        seed_tracks(conn)
        session_id = create_session(conn)
        track_id = conn.execute(
            select(tracks.c.id).where(tracks.c.code == "WS")
        ).scalar_one()
        conn.execute(update(races).where(
            (races.c.session_id == session_id) & (races.c.race_num == 1)
        ).values(track_id=track_id, placement=3))
        conn.execute(insert(import_issues).values(
            session_id=session_id, race_num=1, kind="test"
        ))

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(delete(tracks).where(tracks.c.id == track_id))

    with engine.begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.id == session_id))
    with engine.connect() as conn:
        assert conn.execute(select(races).where(
            races.c.session_id == session_id
        )).first() is None
        assert conn.execute(select(import_issues).where(
            import_issues.c.session_id == session_id
        )).first() is None
        assert conn.execute(select(tracks.c.id).where(
            tracks.c.id == track_id
        )).scalar_one() == track_id


def test_backup_is_complete_and_passes_sqlite_integrity_check(
        engine, tmp_path, monkeypatch):
    """A backup must contain committed data and open as a healthy SQLite DB."""
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(config, "BACKUP_DIR", str(backup_dir))

    with engine.begin() as conn:
        seed_tracks(conn)
        session_id = create_session(conn)
        conn.execute(update(races).where(
            (races.c.session_id == session_id) & (races.c.race_num == 1)
        ).values(placement=4, start_position=9, note="must survive backup"))

    backup_path = backup.run_backup(keep=30)
    assert backup_path is not None

    copied = sqlite3.connect(backup_path)
    try:
        assert copied.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert copied.execute(
            "SELECT placement, start_position, note FROM races "
            "WHERE session_id = ? AND race_num = 1", (session_id,)
        ).fetchone() == (4, 9, "must survive backup")
        assert copied.execute(
            "SELECT COUNT(*) FROM races WHERE session_id = ?", (session_id,)
        ).fetchone() == (12,)
    finally:
        copied.close()


def _run_alembic(root: Path, database: Path, *args: str) -> None:
    env = dict(os.environ, DATABASE_PATH=str(database))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=root, env=env,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_upgrade_preserves_existing_session_and_race_data(tmp_path):
    """Upgrading a populated old revision must be additive and non-destructive."""
    root = Path(__file__).resolve().parent.parent
    database = tmp_path / "old.db"
    _run_alembic(root, database, "upgrade", "0003")

    old = sqlite3.connect(database)
    try:
        track_id = old.execute(
            "SELECT id FROM tracks WHERE code = 'WS'"
        ).fetchone()[0]
        cursor = old.execute(
            "INSERT INTO sessions "
            "(played_at, format, expected_races, notes) VALUES (?, ?, ?, ?)",
            ("2026-08-08 12:00:00", "ffa", 12, "irreplaceable session"),
        )
        session_id = cursor.lastrowid
        old.execute(
            "INSERT INTO races "
            "(session_id, race_num, track_id, placement, start_position, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, 1, track_id, 2, 9, "irreplaceable race"),
        )
        old.commit()
    finally:
        old.close()

    _run_alembic(root, database, "upgrade", "head")

    upgraded = sqlite3.connect(database)
    try:
        assert upgraded.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert upgraded.execute(
            "SELECT notes FROM sessions WHERE id = ?", (session_id,)
        ).fetchone() == ("irreplaceable session",)
        assert upgraded.execute(
            "SELECT placement, start_position, note FROM races "
            "WHERE session_id = ? AND race_num = 1", (session_id,)
        ).fetchone() == (2, 9, "irreplaceable race")
        assert upgraded.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'shock_events'"
        ).fetchone() == ("shock_events",)
    finally:
        upgraded.close()


def test_startup_backs_up_existing_database_before_migrating(
        engine, tmp_path, monkeypatch):
    from app import main

    database = tmp_path / "existing.db"
    database.write_bytes(b"existing database")
    monkeypatch.setattr(config, "DATABASE_PATH", str(database))
    events = []
    monkeypatch.setattr(main.backup, "run_backup", lambda: events.append("backup"))
    monkeypatch.setattr(main.migrate, "upgrade_to_head", lambda: events.append("migrate"))
    monkeypatch.setattr(main.backup, "start_scheduler", lambda: None)

    async def start_and_stop():
        async with main.lifespan(main.app):
            pass

    asyncio.run(start_and_stop())
    assert events == ["backup", "migrate"]


def test_session_deletion_requires_a_successful_backup(
        client, engine, monkeypatch):
    from app.routes import api

    with engine.begin() as conn:
        session_id = create_session(conn)

    def failed_backup():
        raise OSError("backup volume unavailable")

    monkeypatch.setattr(api.backup, "run_backup", failed_backup)
    with pytest.raises(OSError, match="backup volume unavailable"):
        client.delete(f"/api/sessions/{session_id}")

    with engine.connect() as conn:
        assert conn.execute(select(sessions.c.id).where(
            sessions.c.id == session_id
        )).scalar_one() == session_id
        assert conn.execute(select(races.c.id).where(
            races.c.session_id == session_id
        )).first() is not None
