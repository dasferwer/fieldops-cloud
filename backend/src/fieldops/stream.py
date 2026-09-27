"""Журнал инвалидирует клиентский кеш; сами данные выдаются только через защищённый API."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fieldops.models import StreamEvent, StreamState, User, UserRole, WorkOrder

BATCH_SIZE = 100
MAX_REPLAY = 10_000


async def append_event(
    session: AsyncSession, order: WorkOrder, previous_assignee: UUID | None = None
) -> None:
    await session.flush()
    # Обычная sequence выдаёт номера до commit и позволяет пропустить поздний commit.
    # Строка счётчика удерживается до конца той же транзакции, что и изменение заявки.
    position = (
        await session.execute(
            update(StreamState)
            .where(StreamState.id == 1)
            .values(position=StreamState.position + 1)
            .returning(StreamState.position)
        )
    ).scalar_one()
    session.add(
        StreamEvent(
            position=position,
            work_order_id=order.id,
            version=order.version,
            previous_assignee_id=previous_assignee,
        )
    )
    await session.flush()


async def read_batch(session: AsyncSession, user: User, cursor: int) -> dict[str, object]:
    head = (await session.execute(select(StreamState.position))).scalar_one()
    if cursor < 0 or cursor > head or head - cursor > MAX_REPLAY:
        return {"type": "reset", "cursor": head}
    rows = (
        await session.execute(
            select(StreamEvent, WorkOrder.assignee_id)
            .join(WorkOrder, StreamEvent.work_order_id == WorkOrder.id)
            .where(StreamEvent.position > cursor, StreamEvent.position <= head)
            .order_by(StreamEvent.position)
            .limit(BATCH_SIZE)
        )
    ).all()
    events = []
    for event, assignee in rows:
        visible = user.role in {UserRole.ADMIN, UserRole.DISPATCHER} or assignee == user.id
        removed = event.previous_assignee_id == user.id
        if visible or removed:
            events.append(
                {
                    "type": "work_order.changed" if visible else "work_order.removed",
                    "id": str(event.work_order_id),
                }
            )
    return {
        "type": "events",
        "events": events,
        "cursor": rows[-1][0].position if rows else head,
        "more": len(rows) == BATCH_SIZE,
    }
