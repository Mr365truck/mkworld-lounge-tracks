"""add do-not-mogi players

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "do_not_mogi_players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lounge_player_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("lounge_player_id > 0", name="ck_do_not_mogi_player_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lounge_player_id"),
    )


def downgrade() -> None:
    op.drop_table("do_not_mogi_players")
