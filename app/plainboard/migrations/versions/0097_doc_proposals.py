"""doc agents propose larger restructurings of their site (#156)

Revision ID: 0097
Revises: 0096

A switch per doc site (on, also for the sites there are), when its structure was last checked, the proposals, and
the earlier addresses of moved pages (they still find them).
"""
from alembic import op
import sqlalchemy as sa

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("doc_sites") as batch:
        batch.add_column(sa.Column("restructure_check", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("structure_checked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("structure_pages", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("doc_pages") as batch:
        batch.add_column(sa.Column("moved_from", sa.JSON(), nullable=False, server_default="[]"))
    op.create_table(
        "doc_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("doc_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decision_id", sa.Integer(), sa.ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("before", sa.Text(), nullable=False),
        sa.Column("after", sa.Text(), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_doc_proposals_project_id", "doc_proposals", ["project_id"])
    op.create_index("ix_doc_proposals_site_id", "doc_proposals", ["site_id"])
    op.create_index("ix_doc_proposals_decision_id", "doc_proposals", ["decision_id"])


def downgrade() -> None:
    op.drop_table("doc_proposals")
    with op.batch_alter_table("doc_pages") as batch:
        batch.drop_column("moved_from")
    with op.batch_alter_table("doc_sites") as batch:
        batch.drop_column("structure_pages")
        batch.drop_column("structure_checked_at")
        batch.drop_column("restructure_check")
