"""tasks.review_handed_at: tickets left in review go to the manager instead of to done (#114)

Revision ID: 0086
Revises: 0085
"""
import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("review_handed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("review_handed_at")
