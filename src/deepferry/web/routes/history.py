"""GET /history — recent query audit log.

Returns the most recent ``query_history`` rows in descending order.  When no
SQLite connection is configured the endpoint returns an empty list.
"""

from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from deepferry.core.db import get_recent_queries
from deepferry.web.app import get_db

router = APIRouter(prefix="")


@router.get("/history")
async def history(
    limit: int = 50,
    db: aiosqlite.Connection | None = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the most recent query history entries.

    Returns an empty list when the db is not configured; any other failure
    yields a structured 500.
    """
    try:
        if db is None:
            return []
        return await get_recent_queries(db, limit)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "HISTORY_FAILED",
                "message": str(e),
            },
        ) from e
