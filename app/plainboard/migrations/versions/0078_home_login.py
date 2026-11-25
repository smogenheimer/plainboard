"""Installation sign-ins taken over from the built-in runner's home (#84): accounts.home_login

The installation's browser sign-ins (Claude's runner_login, Codex's ChatGPT sign-in) lived in the built-in
runner's home, so they only worked there. They are kept by the app now, like a person's own (#80). The ones
that exist are marked: their files are still in that home, and the app copies them over once (accounts.take_over).

Revision ID: 0078
Revises: 0077
"""
import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("home_login", sa.Boolean(), nullable=False, server_default=sa.false()))
    accounts = sa.table("accounts", sa.column("home_login", sa.Boolean()), sa.column("owner_id", sa.Integer()), sa.column("auth_mode", sa.String()))
    op.execute(accounts.update().where(accounts.c.owner_id.is_(None), accounts.c.auth_mode.in_(("runner_login", "chatgpt"))).values(home_login=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("home_login")
