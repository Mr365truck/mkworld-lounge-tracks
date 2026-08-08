"""add shock location events

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shock_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("lap", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("lap BETWEEN 1 AND 3", name="ck_shock_events_lap"),
        sa.CheckConstraint("x >= 0 AND x <= 1", name="ck_shock_events_x"),
        sa.CheckConstraint("y >= 0 AND y <= 1", name="ck_shock_events_y"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shock_events_track_id", "shock_events", ["track_id"])


def downgrade() -> None:
    op.drop_index("ix_shock_events_track_id", table_name="shock_events")
    op.drop_table("shock_events")
