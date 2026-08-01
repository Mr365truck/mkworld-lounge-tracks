"""initial schema

Spec section 3. Four tables plus an import review queue.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("played_at", sa.DateTime(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("expected_races", sa.Integer(), server_default="12", nullable=False),
        sa.Column("aborted", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("room_min_mmr", sa.Integer(), nullable=True),
        sa.Column("room_max_mmr", sa.Integer(), nullable=True),
        sa.Column("room_avg_mmr", sa.Integer(), nullable=True),
        sa.Column("seat", sa.Integer(), nullable=True),
        sa.Column("mate_mmr", sa.Integer(), nullable=True),
        sa.Column("own_mmr_before", sa.Integer(), nullable=True),
        sa.Column("mmr_delta", sa.Integer(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("format IN ('ffa', '2v2', '3v3', '4v4', '6v6', 'tournament')", name="ck_sessions_format"),
        sa.CheckConstraint("expected_races > 0", name="ck_sessions_expected_races"),
        sa.CheckConstraint("seat IS NULL OR (seat BETWEEN 1 AND 12)", name="ck_sessions_seat"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_played_at", "sessions", ["played_at"])

    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("is_retro", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("has_gate", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("gate_note", sa.Text(), nullable=True),
        sa.Column("good_from_first", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("good_from_first_if_shrooms", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "track_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias"),
    )
    op.create_index("ix_track_aliases_track_id", "track_aliases", ["track_id"])

    op.create_table(
        "races",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("race_num", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("variant", sa.String(), server_default="3lap", nullable=False),
        sa.Column("placement", sa.Integer(), nullable=True),
        sa.Column("start_position", sa.Integer(), nullable=True),
        sa.Column("lap1_position", sa.Integer(), nullable=True),
        sa.Column("shortcut_hit", sa.String(), nullable=True),
        sa.Column("mate_placement", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("shortcut_hit IS NULL OR shortcut_hit IN ('hit', 'miss', 'na')", name="ck_races_shortcut_hit"),
        sa.CheckConstraint("variant IN ('3lap', 'intermission')", name="ck_races_variant"),
        sa.CheckConstraint("lap1_position IS NULL OR (lap1_position BETWEEN 1 AND 12)", name="ck_races_lap1_position"),
        sa.CheckConstraint("mate_placement IS NULL OR (mate_placement BETWEEN 1 AND 12)", name="ck_races_mate_placement"),
        sa.CheckConstraint("placement IS NULL OR (placement BETWEEN 1 AND 12)", name="ck_races_placement"),
        sa.CheckConstraint("race_num > 0", name="ck_races_race_num"),
        sa.CheckConstraint("start_position IS NULL OR (start_position BETWEEN 1 AND 12)", name="ck_races_start_position"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        # Deliberately no unique constraint on (session_id, track_id): the same
        # track legitimately appears twice in one session (spec section 1).
        sa.UniqueConstraint("session_id", "race_num", name="uq_races_session_race_num"),
    )
    op.create_index("ix_races_session_id", "races", ["session_id"])
    op.create_index("ix_races_track_id", "races", ["track_id"])

    op.create_table(
        "import_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("race_num", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("import_issues")
    op.drop_index("ix_races_track_id", table_name="races")
    op.drop_index("ix_races_session_id", table_name="races")
    op.drop_table("races")
    op.drop_index("ix_track_aliases_track_id", table_name="track_aliases")
    op.drop_table("track_aliases")
    op.drop_table("tracks")
    op.drop_index("ix_sessions_played_at", table_name="sessions")
    op.drop_table("sessions")
