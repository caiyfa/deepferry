"""Async SQLite helpers using aiosqlite.

Provides connection lifecycle management, schema initialisation, and
query-audit persistence.  The database is the runtime metadata tier
(separate from the config TOML and the DuckDB analytics tier).
"""

from __future__ import annotations

import aiosqlite


async def get_db(db_path: str) -> aiosqlite.Connection:
    """Create and return an aiosqlite connection.

    Callers are responsible for closing the connection (``await db.close()``)
    or using the connection as an async context manager.
    """
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db(db_path: str) -> None:
    """Create the SQLite tables if they do not exist.

    Tables created:
    * ``query_history`` — audit log of every query executed through deepferry.
    * ``token_cache`` — cached OAuth2 / two-step auth tokens (see two-step-auth spec).
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS query_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id         TEXT NOT NULL,
                query_text        TEXT NOT NULL,
                row_count         INTEGER NOT NULL,
                status            TEXT NOT NULL,
                execution_time_ms REAL NOT NULL,
                created_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS token_cache (
                source_id      TEXT PRIMARY KEY,
                access_token   TEXT NOT NULL,
                refresh_token  TEXT,
                token_type     TEXT NOT NULL DEFAULT 'bearer',
                expires_at     REAL NOT NULL,
                extra          TEXT,
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        await db.commit()


async def record_query(
    db: aiosqlite.Connection,
    source_id: str,
    query_text: str,
    row_count: int,
    status: str,
    execution_time_ms: float,
) -> int:
    """Insert a query execution record and return its auto-generated id."""
    cursor = await db.execute(
        "INSERT INTO query_history (source_id, query_text, row_count, status, execution_time_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_id, query_text, row_count, status, execution_time_ms),
    )
    await db.commit()
    return cursor.lastrowid  # type: ignore[return-value]


async def get_recent_queries(
    db: aiosqlite.Connection,
    limit: int = 50,
) -> list[dict[str, object]]:
    """Return the most recent query history entries."""
    cursor = await db.execute(
        "SELECT * FROM query_history ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]
