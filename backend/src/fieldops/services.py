import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fieldops.models import (
    IdempotencyRecord,
    Site,
    User,
    UserRole,
    WorkOrder,
    WorkOrderEvent,
    WorkOrderPriority,
    WorkOrderStatus,
)
from fieldops.schemas import WorkOrderAssign, WorkOrderCreate, WorkOrderRead, WorkOrderTransition
from fieldops.stream import append_event

ACTIVE_STATUSES = {
    WorkOrderStatus.NEW,
    WorkOrderStatus.ASSIGNED,
    WorkOrderStatus.EN_ROUTE,
    WorkOrderStatus.IN_PROGRESS,
    WorkOrderStatus.BLOCKED,
}

ALLOWED_TRANSITIONS: dict[WorkOrderStatus, set[WorkOrderStatus]] = {
    WorkOrderStatus.NEW: {WorkOrderStatus.ASSIGNED, WorkOrderStatus.CANCELLED},
    WorkOrderStatus.ASSIGNED: {WorkOrderStatus.EN_ROUTE, WorkOrderStatus.CANCELLED},
    WorkOrderStatus.EN_ROUTE: {
        WorkOrderStatus.IN_PROGRESS,
        WorkOrderStatus.BLOCKED,
        WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.IN_PROGRESS: {
        WorkOrderStatus.BLOCKED,
        WorkOrderStatus.COMPLETED,
        WorkOrderStatus.CANCELLED,
    },
    WorkOrderStatus.BLOCKED: {WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.CANCELLED},
    WorkOrderStatus.COMPLETED: set(),
    WorkOrderStatus.CANCELLED: set(),
}


def work_order_query() -> Select[tuple[WorkOrder]]:
    return select(WorkOrder).options(
        selectinload(WorkOrder.site),
        selectinload(WorkOrder.assignee),
    )


def to_work_order_read(order: WorkOrder) -> WorkOrderRead:
    now = datetime.now(UTC)
    return WorkOrderRead(
        id=order.id,
        number=order.number,
        title=order.title,
        description=order.description,
        site_id=order.site_id,
        site_name=order.site.name,
        site_address=order.site.address,
        assignee_id=order.assignee_id,
        assignee_name=order.assignee.full_name if order.assignee else None,
        priority=WorkOrderPriority(order.priority),
        status=WorkOrderStatus(order.status),
        scheduled_for=order.scheduled_for,
        sla_due_at=order.sla_due_at,
        sla_breached=order.status in {status.value for status in ACTIVE_STATUSES}
        and order.sla_due_at < now,
        version=order.version,
        completed_at=order.completed_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def request_hash(payload: WorkOrderCreate) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def new_order_number() -> str:
    return f"WO-{uuid4().hex.upper()}"


async def validate_relations(
    session: AsyncSession,
    site_id: UUID,
    assignee_id: UUID | None,
) -> tuple[Site, User | None]:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")
    assignee = None
    if assignee_id is not None:
        assignee = await session.scalar(
            select(User).where(
                User.id == assignee_id,
                User.role == UserRole.TECHNICIAN,
                User.is_active.is_(True),
            )
        )
        if assignee is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Assignee must be an active technician",
            )
    return site, assignee


async def create_work_order(
    session: AsyncSession,
    actor: User,
    payload: WorkOrderCreate,
    idempotency_key: str,
) -> tuple[WorkOrder, bool]:
    # Один ключ сериализуем до проверки повтора; другие запросы не блокируют друг друга.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 606))"),
        {"key": f"{actor.id}:{idempotency_key}"},
    )
    payload_hash = request_hash(payload)
    existing = await session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == actor.id,
            IdempotencyRecord.key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != payload_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used with a different payload",
            )
        order = await session.scalar(
            work_order_query().where(WorkOrder.id == existing.work_order_id)
        )
        if order is None:
            raise HTTPException(status_code=500, detail="Idempotency record is inconsistent")
        return order, True

    site, assignee = await validate_relations(session, payload.site_id, payload.assignee_id)
    initial_status = WorkOrderStatus.ASSIGNED if assignee else WorkOrderStatus.NEW
    order = WorkOrder(
        number=new_order_number(),
        title=payload.title,
        description=payload.description,
        site_id=site.id,
        assignee_id=assignee.id if assignee else None,
        created_by_id=actor.id,
        priority=payload.priority,
        status=initial_status,
        scheduled_for=payload.scheduled_for,
        sla_due_at=payload.sla_due_at,
    )
    session.add(order)
    await session.flush()
    session.add_all(
        [
            WorkOrderEvent(
                work_order_id=order.id,
                actor_id=actor.id,
                event_type="created",
                to_status=initial_status,
                payload={"priority": payload.priority, "site_id": str(site.id)},
            ),
            IdempotencyRecord(
                actor_id=actor.id,
                key=idempotency_key,
                request_hash=payload_hash,
                work_order_id=order.id,
            ),
        ]
    )
    await append_event(session, order)
    await session.commit()
    loaded = await session.scalar(work_order_query().where(WorkOrder.id == order.id))
    if loaded is None:
        raise HTTPException(status_code=500, detail="Created work order was not found")
    return loaded, False


async def locked_work_order(session: AsyncSession, order_id: UUID) -> WorkOrder:
    order = await session.scalar(
        select(WorkOrder).where(WorkOrder.id == order_id).with_for_update()
    )
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work order not found")
    return order


def assert_version(order: WorkOrder, version: int) -> None:
    if order.version != version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Work order was modified", "current_version": order.version},
        )


def assert_technician_access(order: WorkOrder, actor: User) -> None:
    if UserRole(actor.role) == UserRole.TECHNICIAN and order.assignee_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Work order is not assigned to you"
        )


def assert_transition(current: str, target: WorkOrderStatus) -> None:
    current_status = WorkOrderStatus(current)
    if target not in ALLOWED_TRANSITIONS[current_status]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Transition {current_status.value} -> {target.value} is not allowed",
        )


async def total_for_query(session: AsyncSession, query: Select[tuple[WorkOrder]]) -> int:
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    return int((await session.scalar(count_query)) or 0)


async def assign_work_order(
    session: AsyncSession, actor: User, order_id: UUID, payload: WorkOrderAssign
) -> WorkOrderRead:
    order = await locked_work_order(session, order_id)
    assert_version(order, payload.version)
    if order.status not in ACTIVE_STATUSES:
        raise HTTPException(409, "Нельзя переназначить завершённую или отменённую заявку")
    _, assignee = await validate_relations(session, order.site_id, payload.assignee_id)
    if assignee is None:
        raise HTTPException(422, "Необходимо указать техника")
    previous_assignee, previous_status = order.assignee_id, order.status
    order.assignee_id = assignee.id
    if order.status == WorkOrderStatus.NEW:
        order.status = WorkOrderStatus.ASSIGNED
    order.version += 1
    session.add(
        WorkOrderEvent(
            work_order_id=order.id,
            actor_id=actor.id,
            event_type="assigned",
            from_status=previous_status,
            to_status=WorkOrderStatus(order.status),
            payload={
                "previous_assignee_id": str(previous_assignee) if previous_assignee else None,
                "assignee_id": str(assignee.id),
            },
        )
    )
    await append_event(session, order, previous_assignee)
    await session.commit()
    return await read_changed_order(session, order_id)


async def transition_work_order(
    session: AsyncSession, actor: User, order_id: UUID, payload: WorkOrderTransition
) -> WorkOrderRead:
    order = await locked_work_order(session, order_id)
    assert_technician_access(order, actor)
    assert_version(order, payload.version)
    if actor.role == UserRole.TECHNICIAN and payload.status == WorkOrderStatus.CANCELLED:
        raise HTTPException(403, "Техник не может отменять заявки")
    if payload.status == WorkOrderStatus.ASSIGNED:
        raise HTTPException(409, "Для назначения используйте команду выбора техника")
    if payload.status == WorkOrderStatus.BLOCKED and not (payload.comment or "").strip():
        raise HTTPException(422, "Укажите причину блокировки")
    assert_transition(order.status, payload.status)
    previous = order.status
    order.status = payload.status
    order.version += 1
    if payload.status == WorkOrderStatus.COMPLETED:
        order.completed_at = datetime.now(UTC)
    session.add(
        WorkOrderEvent(
            work_order_id=order.id,
            actor_id=actor.id,
            event_type="status_changed",
            from_status=previous,
            to_status=WorkOrderStatus(order.status),
            comment=payload.comment,
        )
    )
    await append_event(session, order)
    await session.commit()
    return await read_changed_order(session, order_id)


async def read_changed_order(session: AsyncSession, order_id: UUID) -> WorkOrderRead:
    # Повторно загружаем отношения: в identity map мог остаться прежний исполнитель.
    order = await session.scalar(
        work_order_query().where(WorkOrder.id == order_id).execution_options(populate_existing=True)
    )
    if order is None:
        raise HTTPException(404, "Заявка не найдена")
    return to_work_order_read(order)
