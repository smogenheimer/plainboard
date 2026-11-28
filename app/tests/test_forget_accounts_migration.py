"""Migration 0100 (#162): ids of deleted subscriptions are dropped from what still names them."""

import json

import pytest


@pytest.mark.skipif(bool(__import__("os").environ.get("PLAINBOARD_DATABASE")), reason="the path from 0099 exists on SQLite only: PostgreSQL starts at today's tables")
def test_migration_drops_ids_of_deleted_subscriptions(tmp_path, monkeypatch):
    from datetime import datetime

    from alembic import command
    from sqlalchemy import create_engine, text

    from plainboard import config, migrate

    monkeypatch.setenv("PLAINBOARD_DATA_DIR", str(tmp_path / "data"))
    config.get_config.cache_clear()
    (tmp_path / "data").mkdir()
    command.upgrade(migrate.alembic_config(), "0099")
    engine = create_engine(config.get_config().sync_database_url)
    now = datetime(2026, 11, 1)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("DELETE FROM accounts"))  # the sign-ins an earlier migration made accounts of
        for aid in (3, 4):
            conn.execute(
                text("INSERT INTO accounts (id, provider, label, key, auth_mode, enabled, models, created_at, updated_at) VALUES (:id, 'codex', 'c', :key, 'chatgpt', true, '[]', :now, :now)"),
                {"id": aid, "key": f"k{aid}", "now": now},
            )
        conn.execute(
            text(
                "INSERT INTO projects (id, name, slug, description, language, auto_wake_manager, task_counter, account_id, account_ids, blocked_account_ids, created_at, updated_at) "
                "VALUES (1, 'p', 'p', '', '', true, 1, 2, '[2, 4, 3]', '[2]', :now, :now)"
            ),
            {"now": now},
        )
        conn.execute(text("INSERT INTO users (id, username, name, password_hash, is_admin, may_create_projects, account_ids, may_use_shared, enabled, session_version, created_at, updated_at) VALUES (1, 'u', 'u', 'x', false, false, '[2, 3]', false, true, 0, :now, :now)"), {"now": now})
    command.upgrade(migrate.alembic_config(), "head")
    with engine.connect() as conn:
        project = conn.execute(text("SELECT account_id, account_ids, blocked_account_ids FROM projects")).one()
        user_ids = conn.execute(text("SELECT account_ids FROM users")).scalar_one()
    engine.dispose()
    config.get_config.cache_clear()
    as_list = lambda v: v if isinstance(v, list) else json.loads(v)  # noqa: E731
    assert project[0] is None and as_list(project[1]) == [4, 3] and as_list(project[2]) == []
    assert as_list(user_ids) == [3]
