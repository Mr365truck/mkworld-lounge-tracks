"""add current Lounge MMR cache and manual edit timestamp

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("mmr_updated_at", sa.DateTime(), nullable=True))

    op.create_table(
        "lounge_mmr_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lounge_player_id", sa.Integer(), nullable=False),
        sa.Column("player_name", sa.String(), nullable=False),
        sa.Column("mmr", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_lounge_mmr_cache_singleton"),
        sa.CheckConstraint(
            "lounge_player_id > 0", name="ck_lounge_mmr_cache_player_id"
        ),
        sa.CheckConstraint("mmr >= 0", name="ck_lounge_mmr_cache_mmr"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("lounge_mmr_cache")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("mmr_updated_at")
