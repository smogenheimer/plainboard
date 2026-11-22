"""an agent can name a class and a strength instead of a model

Revision ID: 0064
Revises: 0063
"""
from alembic import op
import sqlalchemy as sa

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("model_class", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("strength", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("summary_strength", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("summary_strength")
        batch.drop_column("strength")
        batch.drop_column("model_class")
