"""Doc sites (#118): doc_sites, doc_pages, doc_page_versions, doc_translations, doc_watch, attachments.doc_site_id, agents.doc_site_id, search_documents.doc_page_id

Revision ID: 0088
Revises: 0087
"""
import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def _search_there(bind) -> bool:
    return bind.dialect.name == "postgresql" and bool(bind.execute(sa.text("SELECT to_regclass('search_documents') IS NOT NULL")).scalar())


def upgrade() -> None:
    op.create_table(
        "doc_sites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("public", sa.Boolean(), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("translator", sa.String(10), nullable=False),
        sa.Column("translate_provider", sa.String(50), nullable=False),
        sa.Column("translate_model", sa.String(100), nullable=False),
        sa.Column("deepl_key_enc", sa.Text(), nullable=True),
        sa.Column("google_key_enc", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(253), nullable=True, unique=True),
        sa.Column("domain_token", sa.String(64), nullable=False),
        sa.Column("domain_verified_at", sa.DateTime(), nullable=True),
        sa.Column("cloudflare_token_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_doc_sites_project_id", "doc_sites", ["project_id"])
    op.create_table(
        "doc_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("doc_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("doc_pages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("path", sa.String(2000), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(16), nullable=False),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("edited_by", sa.String(100), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
        sa.Column("drift", sa.Integer(), nullable=False),
        sa.Column("flagged_at", sa.DateTime(), nullable=True),
        sa.Column("flag_reason", sa.Text(), nullable=False),
        sa.Column("reminded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("project_id", "site_id", "parent_id"):
        op.create_index(f"ix_doc_pages_{column}", "doc_pages", [column])
    op.create_index("ix_doc_pages_tree", "doc_pages", ["site_id", "parent_id", "position"])
    op.create_index("ix_doc_pages_path", "doc_pages", ["site_id", "path"])
    op.create_table(
        "doc_page_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("doc_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(100), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_doc_page_versions_project_id", "doc_page_versions", ["project_id"])
    op.create_index("ix_doc_page_versions_page_id", "doc_page_versions", ["page_id"])
    op.create_table(
        "doc_translations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.Integer(), sa.ForeignKey("doc_sites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("doc_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lang", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(16), nullable=False),
        sa.Column("translated_by", sa.String(100), nullable=False),
        sa.Column("translated_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("page_id", "lang"),
    )
    for column in ("project_id", "site_id", "page_id"):
        op.create_index(f"ix_doc_translations_{column}", "doc_translations", [column])
    op.create_table(
        "doc_watch",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("head", sa.String(64), nullable=False),
        sa.Column("landed_at", sa.DateTime(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("attachments") as batch:
        batch.add_column(sa.Column("doc_site_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("attachments_doc_site_id_fkey", "doc_sites", ["doc_site_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_attachments_doc_site_id", ["doc_site_id"])
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("doc_site_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("agents_doc_site_id_fkey", "doc_sites", ["doc_site_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_agents_doc_site_id", ["doc_site_id"])
    if _search_there(op.get_bind()):  # where the search tables are made later, search.schema.ensure links them then
        from plainboard.services.search.schema import link_doc_pages

        link_doc_pages(op.get_bind())


def downgrade() -> None:
    if _search_there(op.get_bind()):
        op.execute("ALTER TABLE search_documents DROP CONSTRAINT IF EXISTS fk_search_documents_doc_page_id")
        op.execute("DROP INDEX IF EXISTS ix_search_documents_doc_page_id")
        op.execute("ALTER TABLE search_documents DROP COLUMN IF EXISTS doc_page_id")
    with op.batch_alter_table("agents") as batch:
        batch.drop_index("ix_agents_doc_site_id")
        batch.drop_constraint("agents_doc_site_id_fkey", type_="foreignkey")
        batch.drop_column("doc_site_id")
    with op.batch_alter_table("attachments") as batch:
        batch.drop_index("ix_attachments_doc_site_id")
        batch.drop_constraint("attachments_doc_site_id_fkey", type_="foreignkey")
        batch.drop_column("doc_site_id")
    op.drop_table("doc_watch")
    op.drop_table("doc_translations")
    op.drop_table("doc_page_versions")
    op.drop_table("doc_pages")
    op.drop_table("doc_sites")
