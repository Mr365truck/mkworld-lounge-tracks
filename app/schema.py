"""SQLAlchemy Core table definitions — spec section 3.

Core, not ORM: four tables, no lazy loading, and `pandas.read_sql` reads straight
off the connection for section 6.
"""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer,
    MetaData, String, Table, Text, UniqueConstraint, func,
)

metadata = MetaData()

FORMATS = ("ffa", "2v2", "3v3", "4v4", "6v6", "tournament")
VARIANTS = ("3lap", "intermission")
SHORTCUT_STATES = ("hit", "miss", "na")

# Room size is fixed at 12 (spec section 3). Supporting 24-player rooms would need a
# `lobby_size` column *and* residual normalization -- raw placements from
# differently-sized rooms are not poolable.
LOBBY_SIZE = 12

tracks = Table(
    "tracks", metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String, nullable=False, unique=True),
    Column("full_name", String, nullable=False),
    Column("is_retro", Boolean, nullable=False, server_default="0"),
    Column("has_gate", Boolean, nullable=False, server_default="0"),
    Column("gate_note", Text),
    Column("good_from_first", Boolean, nullable=False, server_default="0"),
    Column("good_from_first_if_shrooms", Boolean, nullable=False, server_default="0"),
    Column("active", Boolean, nullable=False, server_default="1"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

track_aliases = Table(
    "track_aliases", metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
    # Lowercased on write. Uniqueness is what makes the importer deterministic.
    Column("alias", String, nullable=False, unique=True),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

shock_events = Table(
    "shock_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", Integer, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
    # Coordinates are fractions of the minimap's rendered width/height. Keeping
    # them normalized makes every point survive responsive resizing.
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False),
    Column("lap", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint("x >= 0 AND x <= 1", name="ck_shock_events_x"),
    CheckConstraint("y >= 0 AND y <= 1", name="ck_shock_events_y"),
    CheckConstraint("lap BETWEEN 1 AND 3", name="ck_shock_events_lap"),
)

sessions = Table(
    "sessions", metadata,
    Column("id", Integer, primary_key=True),
    # Stored UTC, rendered through TZ (spec section 8).
    Column("played_at", DateTime, nullable=False),
    Column("format", String, nullable=False),
    # 12, or 8 for a tournament room. Overridable at entry.
    Column("expected_races", Integer, nullable=False, server_default="12"),
    Column("aborted", Boolean, nullable=False, server_default="0"),
    Column("room_min_mmr", Integer),
    Column("room_max_mmr", Integer),
    Column("room_avg_mmr", Integer),
    Column("seat", Integer),
    Column("mate_mmr", Integer),
    Column("own_mmr_before", Integer),
    Column("mmr_delta", Integer),
    Column("score", Integer),
    Column("notes", Text),
    # created_at is deliberately distinct from played_at: section 6 needs when a
    # session was *played*, the importer needs when a row was *entered*.
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    CheckConstraint(f"format IN ({', '.join(repr(f) for f in FORMATS)})", name="ck_sessions_format"),
    CheckConstraint("expected_races > 0", name="ck_sessions_expected_races"),
    CheckConstraint(f"seat IS NULL OR (seat BETWEEN 1 AND {LOBBY_SIZE})", name="ck_sessions_seat"),
)

races = Table(
    "races", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("race_num", Integer, nullable=False),
    # Nullable: the historical doc has numbered races with no track at all.
    Column("track_id", Integer, ForeignKey("tracks.id", ondelete="RESTRICT")),
    Column("variant", String, nullable=False, server_default="3lap"),
    Column("placement", Integer),
    Column("start_position", Integer),
    Column("lap1_position", Integer),
    # Four-state, not a nullable boolean: NULL = not recorded, 'na' = not applicable.
    Column("shortcut_hit", String),
    Column("mate_placement", Integer),
    Column("note", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    # No unique constraint on (session_id, track_id): the same track legitimately
    # appears twice in one session, and that is the case previously miscounted.
    UniqueConstraint("session_id", "race_num", name="uq_races_session_race_num"),
    CheckConstraint(f"variant IN ({', '.join(repr(v) for v in VARIANTS)})", name="ck_races_variant"),
    CheckConstraint(
        f"shortcut_hit IS NULL OR shortcut_hit IN ({', '.join(repr(s) for s in SHORTCUT_STATES)})",
        name="ck_races_shortcut_hit",
    ),
    CheckConstraint("race_num > 0", name="ck_races_race_num"),
    CheckConstraint(f"placement IS NULL OR (placement BETWEEN 1 AND {LOBBY_SIZE})", name="ck_races_placement"),
    CheckConstraint(
        f"start_position IS NULL OR (start_position BETWEEN 1 AND {LOBBY_SIZE})",
        name="ck_races_start_position",
    ),
    CheckConstraint(
        f"lap1_position IS NULL OR (lap1_position BETWEEN 1 AND {LOBBY_SIZE})",
        name="ck_races_lap1_position",
    ),
    CheckConstraint(
        f"mate_placement IS NULL OR (mate_placement BETWEEN 1 AND {LOBBY_SIZE})",
        name="ck_races_mate_placement",
    ),
)

# Anything unresolved by the paste importer lands here rather than failing the whole
# import or being silently dropped (spec section 7).
import_issues = Table(
    "import_issues", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", Integer, ForeignKey("sessions.id", ondelete="CASCADE")),
    Column("race_num", Integer),
    Column("kind", String, nullable=False),
    Column("raw", Text),
    Column("detail", Text),
    Column("resolved", Boolean, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)


def default_expected_races(fmt: str) -> int:
    """Tournament rooms are 8 races; everything else is 12."""
    return 8 if fmt == "tournament" else 12
