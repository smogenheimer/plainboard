"""Ideas of a project (#91): ideas, idea_topics, idea_links, and the model that sorts them (projects.idea_*)

Revision ID: 0080
Revises: 0079
"""
import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

COLUMNS = (("idea_provider", 50), ("idea_model", 100), ("idea_effort", 20))


def upgrade() -> None:
    for name, size in COLUMNS:
        op.add_column("projects", sa.Column(name, sa.String(size), nullable=False, server_default=""))
    op.create_table(
        "idea_topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.Integer(), nullable=False),
        sa.Column("hint", sa.String(300), nullable=False),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_idea_topics_project_id", "idea_topics", ["project_id"])
    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("idea_topics.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("hint", sa.String(300), nullable=False),
        sa.Column("sort_state", sa.String(10), nullable=False),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("votes", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ideas_project_id", "ideas", ["project_id"])
    op.create_index("ix_ideas_status", "ideas", ["status"])
    op.create_index("ix_ideas_topic_id", "ideas", ["topic_id"])
    op.create_table(
        "idea_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idea_id", sa.Integer(), sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("other_id", sa.Integer(), sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
    )
    op.create_index("ix_idea_links_project_id", "idea_links", ["project_id"])
    op.create_index("ix_idea_links_idea_id", "idea_links", ["idea_id"])
    op.create_index("ix_idea_links_other_id", "idea_links", ["other_id"])


def downgrade() -> None:
    op.drop_table("idea_links")
    op.drop_table("ideas")
    op.drop_table("idea_topics")
    with op.batch_alter_table("projects") as batch:
        for name, _ in COLUMNS:
            batch.drop_column(name)
