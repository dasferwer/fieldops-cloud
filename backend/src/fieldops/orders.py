from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select

from fieldops.dependencies import CurrentUser, Dispatcher, SessionDep
from fieldops.models import UserRole, WorkOrder, WorkOrderEvent, WorkOrderPriority, WorkOrderStatus
from fieldops.schemas import (
    WorkOrderAssign,
    WorkOrderCreate,
    WorkOrderEventRead,
    WorkOrderPage,
    WorkOrderRead,
    WorkOrderTransition,
)
from fieldops.services import (
    assert_technician_access,
    assign_work_order,
    create_work_order,
    to_work_order_read,
    transition_work_order,
    work_order_query,
)

router = APIRouter()


@router.get("/api/v1/work-orders", response_model=WorkOrderPage, tags=["Work orders"])
async def list_work_orders(
    session: SessionDep,
    user: CurrentUser,
    status_filter: Annotated[WorkOrderStatus | None, Query(alias="status")] = None,
    priority: WorkOrderPriority | None = None,
    site_id: UUID | None = None,
    assignee_id: UUID | None = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkOrderPage:
    filters: list[Any] = []
    if UserRole(user.role) == UserRole.TECHNICIAN:
        filters.append(WorkOrder.assignee_id == user.id)
    elif assignee_id is not None:
        filters.append(WorkOrder.assignee_id == assignee_id)
    if status_filter is not None:
        filters.append(WorkOrder.status == status_filter)
    if priority is not None:
        filters.append(WorkOrder.priority == priority)
    if site_id is not None:
        filters.append(WorkOrder.site_id == site_id)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(or_(WorkOrder.number.ilike(pattern), WorkOrder.title.ilike(pattern)))

    total = int(
        (await session.scalar(select(func.count()).select_from(WorkOrder).where(*filters))) or 0
    )
    query = (
        work_order_query()
        .where(*filters)
        .order_by(
            WorkOrder.scheduled_for,
            WorkOrder.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    items = list((await session.scalars(query)).all())
    return WorkOrderPage(
        items=[to_work_order_read(order) for order in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/api/v1/work-orders/{order_id}", response_model=WorkOrderRead, tags=["Work orders"])
async def get_work_order(order_id: UUID, session: SessionDep, user: CurrentUser) -> WorkOrderRead:
    order = await session.scalar(work_order_query().where(WorkOrder.id == order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    assert_technician_access(order, user)
    return to_work_order_read(order)


@router.post(
    "/api/v1/work-orders",
    response_model=WorkOrderRead,
    status_code=status.HTTP_201_CREATED,
    tags=["Work orders"],
)
async def create_order(
    payload: WorkOrderCreate,
    response: Response,
    session: SessionDep,
    actor: Dispatcher,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=120),
    ],
) -> WorkOrderRead:
    order, replayed = await create_work_order(session, actor, payload, idempotency_key)
    if replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    result = to_work_order_read(order)
    return result


@router.post(
    "/api/v1/work-orders/{order_id}/assign",
    response_model=WorkOrderRead,
    tags=["Work orders"],
)
async def assign_order(
    order_id: UUID,
    payload: WorkOrderAssign,
    session: SessionDep,
    actor: Dispatcher,
) -> WorkOrderRead:
    return await assign_work_order(session, actor, order_id, payload)


@router.post(
    "/api/v1/work-orders/{order_id}/transition",
    response_model=WorkOrderRead,
    tags=["Work orders"],
)
async def transition_order(
    order_id: UUID,
    payload: WorkOrderTransition,
    session: SessionDep,
    actor: CurrentUser,
) -> WorkOrderRead:
    return await transition_work_order(session, actor, order_id, payload)


@router.get(
    "/api/v1/work-orders/{order_id}/events",
    response_model=list[WorkOrderEventRead],
    tags=["Work orders"],
)
async def order_events(
    order_id: UUID,
    session: SessionDep,
    user: CurrentUser,
) -> list[WorkOrderEvent]:
    order = await session.get(WorkOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    assert_technician_access(order, user)
    query = (
        select(WorkOrderEvent)
        .where(WorkOrderEvent.work_order_id == order_id)
        .order_by(WorkOrderEvent.occurred_at)
    )
    return list((await session.scalars(query)).all())
