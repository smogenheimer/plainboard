"""Semantic search: text files attached to tickets and ideas (kind file, #157); deleting the attachment deletes its chunks

Revision ID: 0098
Revises: 0097
"""
import sqlalchemy as sa
from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def _there(bind) -> bool:
    return bind.dialect.name == "postgresql" and bool(bind.execute(sa.text("SELECT to_regclass('search_documents') IS NOT NULL")).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if not _there(bind):  # SQLite, or no pgvector: the table is made with this column where it comes later
        return
    # IF NOT EXISTS: made by 0073 already where it ran with today's models. The files are indexed by the watcher from its
    # cursor "file", which starts at the beginning: the backfill.
    op.execute("ALTER TABLE search_documents ADD COLUMN IF NOT EXISTS attachment_id INTEGER REFERENCES attachments(id) ON DELETE CASCADE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_search_documents_attachment_id ON search_documents (attachment_id)")


def downgrade() -> None:
    if not _there(op.get_bind()):
        return
    op.execute("DELETE FROM search_documents WHERE kind = 'file'")
    op.execute("DELETE FROM search_cursors WHERE source = 'file'")
    op.execute("DROP INDEX IF EXISTS ix_search_documents_attachment_id")
    op.execute("ALTER TABLE search_documents DROP COLUMN IF EXISTS attachment_id")
