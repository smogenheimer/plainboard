"""ideas handed over to the manager (#163)

Revision ID: 0101
Revises: 0100

When a person handed an idea to the manager to make tickets of; the board shows it until the idea is taken or left out.
"""
from alembic import op
import sqlalchemy as sa

revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ideas") as batch:
        batch.add_column(sa.Column("handed_over_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ideas") as batch:
        batch.drop_column("handed_over_at")
