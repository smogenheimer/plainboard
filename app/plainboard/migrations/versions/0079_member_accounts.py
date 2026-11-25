"""Members give their own subscriptions to a project themselves (#93): accounts.project_ids, projects.blocked_account_ids

A person's own subscription (#80) could only be used by projects whose owner it was shared with. Now its owner
gives it to projects they work in (``Account.project_ids``); the project's owner can switch such a one off for
the project (``Project.blocked_account_ids``).

Revision ID: 0079
Revises: 0078
"""
import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("project_ids", sa.JSON(), nullable=True))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("blocked_account_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("blocked_account_ids")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("project_ids")
