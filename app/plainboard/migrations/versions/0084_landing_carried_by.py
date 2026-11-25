"""A branch a repair branch carries points at that landing (#98): landings.carried_by

Revision ID: 0084
Revises: 0083
"""
import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("landings", sa.Column("carried_by", sa.Integer(), nullable=True))
    op.create_index("ix_landings_carried_by", "landings", ["carried_by"])


def downgrade() -> None:
    op.drop_index("ix_landings_carried_by", table_name="landings")
    op.drop_column("landings", "carried_by")
