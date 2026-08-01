"""seed track and alias reference data

Spec section 9 step 1: get the reference data right first, because everything else
depends on it. The seed itself lives in app/seed_data.py (Appendix A) and is applied
idempotently, so re-running it after a code edit adds new rows without clobbering the
`good_from_first` / `gate_note` / `active` values set from the Settings screen.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

from app.seed import seed_tracks

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    seed_tracks(op.get_bind())


def downgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql("DELETE FROM track_aliases")
    conn.exec_driver_sql("DELETE FROM tracks")
