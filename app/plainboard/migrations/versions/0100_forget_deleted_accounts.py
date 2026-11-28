"""drop mentions of deleted subscriptions (#162)

Revision ID: 0100
Revises: 0099

Deleting a subscription left its id in projects.account_ids, projects.blocked_account_ids and users.account_ids
(and, on SQLite without the foreign key in force, in projects.account_id and agents.account_id): saving such a
project's settings then failed with "Account not found". Ids of subscriptions that no longer exist are removed.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def _ids(value) -> list | None:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) else None


def upgrade() -> None:
    bind = op.get_bind()
    known = {row[0] for row in bind.execute(sa.text("SELECT id FROM accounts"))}
    for table, columns in (("projects", ("account_ids", "blocked_account_ids")), ("users", ("account_ids",))):
        t = sa.table(table, sa.column("id", sa.Integer()), *(sa.column(c, sa.JSON()) for c in columns))
        for row in bind.execute(sa.select(t)).mappings().all():
            changes = {}
            for column in columns:
                ids = _ids(row[column])
                if ids is not None and any(i not in known for i in ids):
                    changes[column] = [i for i in ids if i in known]
            if changes:
                bind.execute(t.update().where(t.c.id == row["id"]).values(**changes))
    for table in ("projects", "agents"):
        bind.execute(sa.text(f"UPDATE {table} SET account_id = NULL WHERE account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM accounts)"))


def downgrade() -> None:
    pass
