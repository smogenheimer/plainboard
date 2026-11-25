"""a project may switch off pausing before its limits

Revision ID: 0096
Revises: 0095

On, also for the projects there are: off, a project never pauses before its subscriptions run out.
"""
from alembic import op
import sqlalchemy as sa

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("limit_pause", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("limit_pause")
