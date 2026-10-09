"""Операторская пакетная очистка: без --apply выполняется только dry-run."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldops.config import Settings, get_settings
from fieldops.db import SessionFactory, engine
from fieldops.models import StreamEvent, StreamState


class CleanupResult(TypedDict):
    enabled: bool
    dry_run: bool
    candidates: int
    deleted: int
    retained_after: int
    delete_through: int


async def clean_stream(
    session: AsyncSession,
    settings: Settings,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> CleanupResult:
    """Удаление и граница атомарны; commit/rollback выполняет вызывающий код."""
    result: CleanupResult = {
        "enabled": settings.stream_retention_seconds > 0,
        "dry_run": dry_run,
        "candidates": 0,
        "deleted": 0,
        "retained_after": 0,
        "delete_through": 0,
    }
    if not result["enabled"]:
        return result
    state = (
        await session.scalars(select(StreamState).where(StreamState.id == 1).with_for_update())
    ).one()
    result["retained_after"] = result["delete_through"] = state.retained_after
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=settings.stream_retention_seconds)
    rows = (
        await session.execute(
            select(StreamEvent.position, StreamEvent.occurred_at)
            .where(
                StreamEvent.position > state.retained_after, StreamEvent.position <= state.position
            )
            .order_by(StreamEvent.position)
            .limit(settings.stream_retention_batch_size)
        )
    ).all()
    # Часы приложения могут идти назад: после первого недавнего события префикс закончен.
    for position, occurred_at in rows:
        if occurred_at >= cutoff:
            break
        result["candidates"] += 1
        result["delete_through"] = position
    if not dry_run and result["candidates"]:
        await session.execute(
            delete(StreamEvent).where(StreamEvent.position <= result["delete_through"])
        )
        state.retained_after = result["delete_through"]
        await session.flush()
        result["deleted"] = result["candidates"]
        result["retained_after"] = state.retained_after
    return result


async def run_cleanup(*, apply: bool) -> None:
    try:
        async with SessionFactory() as session, session.begin():
            result = await clean_stream(session, get_settings(), dry_run=not apply)
        print(json.dumps(result))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Удалить один пакет вместо dry-run")
    args = parser.parse_args()
    asyncio.run(run_cleanup(apply=args.apply))


if __name__ == "__main__":
    main()
