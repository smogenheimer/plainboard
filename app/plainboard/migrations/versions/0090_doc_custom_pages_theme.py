"""A doc site's own pages for its footer (imprint, privacy) and its theme (#121): doc_custom_pages, doc_sites.theme.

Revision ID: 0090
Revises: 0089
"""
import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("doc_sites") as batch:
        batch.add_column(sa.Column("theme", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "doc_custom_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("doc_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("texts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("edited_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("site_id", "slug"),
    )
    op.create_index("ix_doc_custom_pages_project_id", "doc_custom_pages", ["project_id"])
    op.create_index("ix_doc_custom_pages_site_id", "doc_custom_pages", ["site_id"])


def downgrade() -> None:
    op.drop_table("doc_custom_pages")
    with op.batch_alter_table("doc_sites") as batch:
        batch.drop_column("theme")
