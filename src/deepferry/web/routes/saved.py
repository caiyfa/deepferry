"""Saved-query CRUD REST endpoints.

Thin HTTP layer over the ``saved_queries`` SQLite table.  All mutations are
idempotent where practical; duplicate names yield a structured 409.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deepferry.core.query_template import extract_params
from deepferry.web.app import get_db

router = APIRouter(prefix="/saved-queries", tags=["saved-queries"])


# ── Pydantic models ──────────────────────────────────────────────────────────


class SavedQueryCreate(BaseModel):
    name: str
    description: str | None = None
    source_ids: list[str]
    statement: str
    tags: list[str] = []


class SavedQueryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    source_ids: list[str] | None = None
    statement: str | None = None
    tags: list[str] | None = None


class SavedQuery(BaseModel):
    id: int
    name: str
    description: str | None = None
    source_ids: list[str]
    statement: str
    tags: list[str] = []
    params: list[str] = []
    created_at: str | None = None
    updated_at: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _row_to_saved(row: aiosqlite.Row) -> SavedQuery:
    """Deserialise a raw ``aiosqlite.Row`` into a ``SavedQuery`` model."""
    source_ids: list[str] = json.loads(row["source_ids"])
    tags: list[str] = json.loads(row["tags"]) if row["tags"] else []
    statement: str = row["statement"]
    return SavedQuery(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        source_ids=source_ids,
        statement=statement,
        tags=tags,
        params=extract_params(statement),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _deser_tags(tags: list[str] | None) -> str:
    """Encode *tags* as a JSON string for storage (None → ``"[]"``)."""
    return json.dumps(tags if tags is not None else [])


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("")
async def list_saved(
    db: aiosqlite.Connection | None = Depends(get_db),
) -> list[SavedQuery]:
    """Return every saved query, newest first."""
    if db is None:
        return []
    cursor = await db.execute(
        "SELECT id, name, description, source_ids, statement, tags, "
        "created_at, updated_at "
        "FROM saved_queries ORDER BY id DESC"
    )
    rows = await cursor.fetchall()
    return [_row_to_saved(r) for r in rows]


@router.get("/{saved_id}")
async def get_saved(
    saved_id: int,
    db: aiosqlite.Connection | None = Depends(get_db),
) -> SavedQuery:
    """Return a single saved query by id (404 if not found)."""
    if db is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Saved query {saved_id} not found"},
        )
    cursor = await db.execute(
        "SELECT id, name, description, source_ids, statement, tags, "
        "created_at, updated_at "
        "FROM saved_queries WHERE id = ?",
        (saved_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Saved query {saved_id} not found"},
        )
    return _row_to_saved(row)


@router.post("", status_code=201)
async def create_saved(
    body: SavedQueryCreate,
    db: aiosqlite.Connection | None = Depends(get_db),
) -> SavedQuery:
    """Persist a new saved query (409 on duplicate name)."""
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "NO_DB", "message": "Persistence is disabled."},
        )
    try:
        cursor = await db.execute(
            "INSERT INTO saved_queries (name, description, source_ids, statement, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                body.name,
                body.description,
                json.dumps(body.source_ids),
                body.statement,
                _deser_tags(body.tags),
            ),
        )
        await db.commit()
    except aiosqlite.IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DUPLICATE_NAME",
                "message": f"A saved query named '{body.name}' already exists.",
            },
        ) from e

    new_id = cursor.lastrowid
    assert new_id is not None
    row = await db.execute(
        "SELECT id, name, description, source_ids, statement, tags, "
        "created_at, updated_at "
        "FROM saved_queries WHERE id = ?",
        (new_id,),
    )
    created = await row.fetchone()
    assert created is not None
    return _row_to_saved(created)


@router.put("/{saved_id}")
async def update_saved(
    saved_id: int,
    body: SavedQueryUpdate,
    db: aiosqlite.Connection | None = Depends(get_db),
) -> SavedQuery:
    """Partially update a saved query (404 if not found, 409 on name conflict)."""
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "NO_DB", "message": "Persistence is disabled."},
        )

    # Verify existence first.
    cursor = await db.execute(
        "SELECT id FROM saved_queries WHERE id = ?", (saved_id,)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Saved query {saved_id} not found"},
        )

    # Build SET clause from non-None fields.
    set_parts: list[str] = []
    params: list[Any] = []
    if body.name is not None:
        set_parts.append("name = ?")
        params.append(body.name)
    if body.description is not None:
        set_parts.append("description = ?")
        params.append(body.description)
    if body.source_ids is not None:
        set_parts.append("source_ids = ?")
        params.append(json.dumps(body.source_ids))
    if body.statement is not None:
        set_parts.append("statement = ?")
        params.append(body.statement)
    if body.tags is not None:
        set_parts.append("tags = ?")
        params.append(_deser_tags(body.tags))

    if not set_parts:
        # Nothing to update — return current state.
        row = await db.execute(
            "SELECT id, name, description, source_ids, statement, tags, "
            "created_at, updated_at "
            "FROM saved_queries WHERE id = ?",
            (saved_id,),
        )
        current = await row.fetchone()
        assert current is not None
        return _row_to_saved(current)

    set_parts.append("updated_at = datetime('now')")
    params.append(saved_id)

    try:
        await db.execute(
            f"UPDATE saved_queries SET {', '.join(set_parts)} WHERE id = ?",
            params,
        )
        await db.commit()
    except aiosqlite.IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DUPLICATE_NAME",
                "message": f"A saved query named '{body.name}' already exists.",
            },
        ) from e

    row = await db.execute(
        "SELECT id, name, description, source_ids, statement, tags, "
        "created_at, updated_at "
        "FROM saved_queries WHERE id = ?",
        (saved_id,),
    )
    updated = await row.fetchone()
    assert updated is not None
    return _row_to_saved(updated)


@router.delete("/{saved_id}")
async def delete_saved(
    saved_id: int,
    db: aiosqlite.Connection | None = Depends(get_db),
) -> dict[str, bool]:
    """Delete a saved query (404 if not found)."""
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "NO_DB", "message": "Persistence is disabled."},
        )
    cursor = await db.execute(
        "SELECT id FROM saved_queries WHERE id = ?", (saved_id,)
    )
    if await cursor.fetchone() is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Saved query {saved_id} not found"},
        )
    await db.execute("DELETE FROM saved_queries WHERE id = ?", (saved_id,))
    await db.commit()
    return {"deleted": True}
