"""Files attached to ideas (#100): attachments.idea_id

Revision ID: 0085
Revises: 0084
"""
import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(sa.Column("idea_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_attachments_idea_id_ideas", "ideas", ["idea_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_attachments_idea_id", ["idea_id"])


def downgrade() -> None:
    with op.batch_alter_table("attachments") as batch:
        batch.drop_index("ix_attachments_idea_id")
        batch.drop_constraint("fk_attachments_idea_id_ideas", type_="foreignkey")
        batch.drop_column("idea_id")
