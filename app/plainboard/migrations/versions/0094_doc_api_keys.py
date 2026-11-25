"""Read-only API keys of doc sites (#131): doc_api_keys.

Revision ID: 0094
Revises: 0093
"""
import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("doc_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("hint", sa.String(8), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_doc_api_keys_project_id", "doc_api_keys", ["project_id"])
    op.create_index("ix_doc_api_keys_site_id", "doc_api_keys", ["site_id"])


def downgrade() -> None:
    op.drop_table("doc_api_keys")
