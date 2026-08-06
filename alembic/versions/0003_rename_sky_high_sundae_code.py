"""rename Sky-High Sundae's canonical code to rSHS

Revision ID: 0003
Revises: 0002
"""
from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "UPDATE tracks SET code = 'rSHS', updated_at = CURRENT_TIMESTAMP "
        "WHERE code = 'SHS'"
    )
    conn.exec_driver_sql(
        "INSERT OR IGNORE INTO track_aliases (track_id, alias) "
        "SELECT id, 'rshs' FROM tracks WHERE code = 'rSHS'"
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "DELETE FROM track_aliases WHERE alias = 'rshs' "
        "AND track_id = (SELECT id FROM tracks WHERE code = 'rSHS')"
    )
    conn.exec_driver_sql(
        "UPDATE tracks SET code = 'SHS', updated_at = CURRENT_TIMESTAMP "
        "WHERE code = 'rSHS'"
    )
