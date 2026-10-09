"""Очистка удаляет только непрерывный префикс и сообщает клиенту потерю replay."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update

from fieldops.config import Settings
from fieldops.db import SessionFactory
from fieldops.models import StreamEvent, StreamState, User, WorkOrder
from fieldops.stream import read_batch

pytestmark = pytest.mark.usefixtures("preserve_journal")


async def test_cleanup_cli_is_disabled_by_default() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fieldops.retention", "--apply"],
        env={**os.environ, "FIELDOPS_STREAM_RETENTION_SECONDS": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["enabled"] is False


async def test_prefix_cleanup_dry_run_batch_and_cursor_boundary() -> None:
    from fieldops.retention import clean_stream

    now = datetime.now(UTC)
    settings = Settings(stream_retention_seconds=3600, stream_retention_batch_size=2)
    async with SessionFactory() as session:
        state = (await session.scalars(select(StreamState).with_for_update())).one()
        start = state.position
        order = (await session.scalars(select(WorkOrder))).first()
        user = (await session.scalars(select(User).where(User.role == "dispatcher"))).first()
        assert order is not None and user is not None
        # Недавнее событие между старыми запрещает удалять разрозненные позиции.
        for offset, age in enumerate([7200, 7200, 7200, 0, 7200], 1):
            session.add(
                StreamEvent(
                    position=start + offset,
                    work_order_id=order.id,
                    version=1,
                    occurred_at=now - timedelta(seconds=age),
                )
            )
        state.position = start + 5
        await session.flush()
        await session.execute(
            update(StreamEvent)
            .where(StreamEvent.position <= start)
            .values(occurred_at=now - timedelta(seconds=7200))
        )
        count = (await session.execute(select(func.count()).select_from(StreamEvent))).scalar_one()
        before_floor = state.retained_after
        preview = await clean_stream(session, settings, now=now)
        assert preview["deleted"] == 0 and preview["candidates"] == 2
        assert state.retained_after == before_floor
        assert (
            await session.execute(select(func.count()).select_from(StreamEvent))
        ).scalar_one() == count
        while state.retained_after < start + 3:
            result = await clean_stream(session, settings, dry_run=False, now=now)
            assert 0 < result["deleted"] <= 2
        assert state.retained_after == start + 3
        assert await read_batch(session, user, start + 2) == {"type": "reset", "cursor": start + 5}
        replay = await read_batch(session, user, start + 3)
        assert replay["type"] == "events" and replay["cursor"] == start + 5
        assert len(replay["events"]) == 2
        assert (await clean_stream(session, settings, dry_run=False, now=now))["deleted"] == 0
        await session.rollback()


async def test_cleanup_all_events_preserves_head_and_rolls_back_atomically() -> None:
    from fieldops.retention import clean_stream

    now = datetime.now(UTC)
    async with SessionFactory() as session:
        state = (await session.scalars(select(StreamState).with_for_update())).one()
        before_head, before_floor = state.position, state.retained_after
        order = (await session.scalars(select(WorkOrder))).first()
        assert order is not None
        state.position += 1
        head = state.position
        session.add(StreamEvent(position=head, work_order_id=order.id, version=1))
        await session.flush()
        user = (await session.scalars(select(User).where(User.role == "dispatcher"))).first()
        assert user is not None
        await session.execute(update(StreamEvent).values(occurred_at=now - timedelta(days=2)))
        result = await clean_stream(
            session, Settings(stream_retention_seconds=1), dry_run=False, now=now
        )
        assert result["deleted"] > 0
        assert state.position == head and state.retained_after == head
        assert await read_batch(session, user, head - 1) == {
            "type": "reset",
            "cursor": head,
        }
        assert (await read_batch(session, user, head))["type"] == "events"
        await session.rollback()
        persisted = (
            await session.execute(select(StreamState.position, StreamState.retained_after))
        ).one()
        assert tuple(persisted) == (before_head, before_floor)


@pytest.mark.parametrize("seconds,batch", [(-1, 100), (1, 0), (1, 10001)])
def test_invalid_retention_policy_is_rejected(seconds: int, batch: int) -> None:
    with pytest.raises(ValidationError):
        Settings(stream_retention_seconds=seconds, stream_retention_batch_size=batch)


async def test_read_resets_when_cleanup_commits_between_state_and_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Барьер ставится на реальный запрос, очистка выполняется другой транзакцией.
    from fieldops.retention import clean_stream

    async with SessionFactory() as session, session.begin():
        state = (await session.scalars(select(StreamState).with_for_update())).one()
        cursor = state.position
        order = (await session.scalars(select(WorkOrder))).first()
        assert order is not None
        state.position += 1
        session.add(
            StreamEvent(
                position=state.position,
                work_order_id=order.id,
                version=1,
                occurred_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
    async with SessionFactory() as session:
        user = (await session.scalars(select(User).where(User.role == "dispatcher"))).first()
        assert user is not None
        real_execute = session.execute
        first = True

        async def execute_then_cleanup(*args: object, **kwargs: object) -> object:
            nonlocal first
            result = await real_execute(*args, **kwargs)
            if first:
                first = False
                async with SessionFactory() as cleaner, cleaner.begin():
                    await cleaner.execute(
                        update(StreamEvent).values(
                            occurred_at=datetime.now(UTC) - timedelta(days=2)
                        )
                    )
                    await clean_stream(cleaner, Settings(stream_retention_seconds=1), dry_run=False)
            return result

        monkeypatch.setattr(session, "execute", execute_then_cleanup)
        assert (await read_batch(session, user, cursor))["type"] == "reset"


async def test_replay_limit_is_still_enforced() -> None:
    async with SessionFactory() as session:
        state = (await session.scalars(select(StreamState).with_for_update())).one()
        user = (await session.scalars(select(User).where(User.role == "dispatcher"))).first()
        assert user is not None
        state.position = state.retained_after + 10_001
        await session.flush()
        assert (await read_batch(session, user, state.retained_after))["type"] == "reset"
        assert (await read_batch(session, user, state.retained_after + 1))["type"] == "events"
        await session.rollback()


async def test_migration_downgrade_refuses_to_forget_retention_boundary() -> None:
    from fieldops.retention import clean_stream

    async with SessionFactory() as session, session.begin():
        state = (await session.scalars(select(StreamState).with_for_update())).one()
        order = (await session.scalars(select(WorkOrder))).first()
        assert order is not None
        state.position += 1
        session.add(
            StreamEvent(
                position=state.position,
                work_order_id=order.id,
                version=1,
                occurred_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        await session.flush()
        await clean_stream(session, Settings(stream_retention_seconds=1), dry_run=False)
        before = (state.position, state.retained_after)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "20260926_02"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "Откат потерял границу удалённого префикса"
    assert "retained_after" in result.stderr
    async with SessionFactory() as session:
        persisted = (
            await session.execute(select(StreamState.position, StreamState.retained_after))
        ).one()
        assert tuple(persisted) == before
