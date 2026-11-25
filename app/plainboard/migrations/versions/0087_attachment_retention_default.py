"""Attachment retention has a PlainBoard default (#113): projects.attachment_retention_days / _min_mb may be
NULL, which follows the default in the settings. Projects that never delete (0, every project so far, and the
default starts at never) follow the default from now on; one with a value of its own keeps it. No data goes.

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("attachment_retention_days", existing_type=sa.Integer(), nullable=True, server_default=None)
        batch.alter_column("attachment_retention_min_mb", existing_type=sa.Integer(), nullable=True, server_default=None)
    op.execute("UPDATE projects SET attachment_retention_days = NULL, attachment_retention_min_mb = NULL WHERE attachment_retention_days = 0 AND attachment_retention_min_mb = 0")


def downgrade() -> None:
    op.execute("UPDATE projects SET attachment_retention_days = 0 WHERE attachment_retention_days IS NULL")
    op.execute("UPDATE projects SET attachment_retention_min_mb = 0 WHERE attachment_retention_min_mb IS NULL")
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("attachment_retention_days", existing_type=sa.Integer(), nullable=False, server_default="0")
        batch.alter_column("attachment_retention_min_mb", existing_type=sa.Integer(), nullable=False, server_default="0")
