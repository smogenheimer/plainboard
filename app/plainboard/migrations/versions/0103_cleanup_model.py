"""The model of a project's cleanup runs (#180): projects.cleanup_provider, cleanup_model

Revision ID: 0103
Revises: 0102
"""
import sqlalchemy as sa
from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

COLUMNS = (("cleanup_provider", 50), ("cleanup_model", 100))


def upgrade() -> None:
    for name, size in COLUMNS:
        op.add_column("projects", sa.Column(name, sa.String(size), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        for name, _ in COLUMNS:
            batch.drop_column(name)
