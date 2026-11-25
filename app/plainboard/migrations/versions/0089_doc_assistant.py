"""A doc site's assistant for visitors (#119): doc_sites.assistant_* (off by default).

Revision ID: 0089
Revises: 0088
"""
import sqlalchemy as sa
from alembic import op

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column("assistant_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("assistant_provider", sa.String(50), nullable=False, server_default=""),
    sa.Column("assistant_model", sa.String(100), nullable=False, server_default=""),
    sa.Column("assistant_effort", sa.String(20), nullable=False, server_default=""),
    sa.Column("assistant_code", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("assistant_daily_messages", sa.Integer(), nullable=False, server_default="300"),
    sa.Column("assistant_daily_cost", sa.Float(), nullable=False, server_default="0"),
    sa.Column("assistant_max_tokens", sa.Integer(), nullable=False, server_default="1024"),
]


def upgrade() -> None:
    with op.batch_alter_table("doc_sites") as batch:
        for column in COLUMNS:
            batch.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("doc_sites") as batch:
        for column in COLUMNS:
            batch.drop_column(column.name)
