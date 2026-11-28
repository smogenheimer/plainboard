"""a failed turn that a usage limit or an overload ended is marked (#5)

Revision ID: 0100
Revises: 0099

Such a turn is waiting, not a failure of the work: statistics count it apart from real failures, and an
assignment cut off this way goes on after the reset, also across a restart. Turns before this are unmarked.
"""
from alembic import op
import sqlalchemy as sa

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("limited", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("limited")
