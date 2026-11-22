"""Arena's public leaderboards (arena.ai), read once when the user asks for it: the ratings the
model classes' scores can start from (``model_choice.fill``). Arena has no API; it publishes the
leaderboards as a dataset, which Hugging Face serves row by row without a key. Nothing about this
installation is sent."""

from __future__ import annotations

import asyncio

import httpx

from .errors import ServiceError

ROWS = "https://datasets-server.huggingface.co/rows"
DATASET = "lmarena-ai/leaderboard-dataset"
PAGE = 100  # the most the server gives at once


async def _board(client: httpx.AsyncClient, board: str) -> dict[str, float]:
    """One leaderboard as it stands now: Arena's model name -> rating, over all kinds of questions."""

    async def page(offset: int) -> dict:
        resp = await client.get(ROWS, params={"dataset": DATASET, "config": board, "split": "latest", "offset": offset, "length": PAGE})
        resp.raise_for_status()
        return resp.json()

    first = await page(0)
    rest = await asyncio.gather(*(page(offset) for offset in range(PAGE, int(first.get("num_rows_total") or 0), PAGE)))
    rows = [r["row"] for part in (first, *rest) for r in part.get("rows", [])]
    return {r["model_name"]: float(r["rating"]) for r in rows if r.get("category") == "overall" and r.get("rating") is not None}


async def ratings(boards: dict[str, str]) -> dict[str, dict[str, float]]:
    """``boards``: class -> leaderboard. Returns class -> Arena's model name -> rating."""
    try:
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=6)) as client:
            found = await asyncio.gather(*(_board(client, board) for board in boards.values()))
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ServiceError(f"Arena's leaderboards cannot be read right now: {exc}") from exc
    return dict(zip(boards, found, strict=True))
