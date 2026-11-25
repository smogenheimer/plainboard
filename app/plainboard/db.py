from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def without_nul(value: Any) -> Any:
    """The value without NUL characters in its strings (keys too). PostgreSQL can't store them in text,
    and one in a JSON column breaks every ``->``/``->>`` on that row (tool output from runners has them, #120)."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {without_nul(k): without_nul(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [without_nul(v) for v in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(without_nul(value))


def init_engine(url: str) -> AsyncEngine:
    global _engine, _sessionmaker
    if not url.startswith("sqlite"):
        _engine = create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, json_serializer=_json)  # PostgreSQL
    else:
        _engine = create_async_engine(url, connect_args={"timeout": 30}, json_serializer=_json)

        @event.listens_for(_engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            # In WAL mode safe against corruption, and a sync per checkpoint instead of per commit.
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA journal_size_limit=67108864")  # the WAL file shrinks back to 64 MB after a checkpoint
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    assert _sessionmaker is not None, "init_engine() not called"
    return _sessionmaker


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session
