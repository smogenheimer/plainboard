"""a project pauses by itself while all its subscriptions are nearly used up

Revision ID: 0095
Revises: 0094

Since when, and when it is expected to go on (empty: unknown). Empty since: it is not paused.
"""
from alembic import op
import sqlalchemy as sa

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("limit_paused_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("limit_resumes_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("limit_resumes_at")
        batch.drop_column("limit_paused_at")
