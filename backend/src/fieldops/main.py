from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, or_, select, true
from sqlalchemy.sql.elements import ColumnElement

from fieldops.config import get_settings
from fieldops.db import SessionFactory
from fieldops.dependencies import CurrentUser, Dispatcher, SessionDep
from fieldops.models import (
    Site,
    User,
    UserRole,
    WorkOrder,
    WorkOrderEvent,
    WorkOrderPriority,
    WorkOrderStatus,
)
from fieldops.realtime import manager
from fieldops.schemas import (
    DashboardStats,
    HealthResponse,
    SiteRead,
    TokenResponse,
    UserRead,
    WorkOrderAssign,
    WorkOrderCreate,
    WorkOrderEventRead,
    WorkOrderPage,
    WorkOrderRead,
    WorkOrderTransition,
)
from fieldops.security import create_access_token, decode_access_token, verify_password
from fieldops.services import (
    ACTIVE_STATUSES,
    assert_technician_access,
    assert_transition,
    assert_version,
    create_work_order,
    locked_work_order,
    to_work_order_read,
    validate_relations,
    work_order_query,
)

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    summary="Field service operations with SLA and real-time dispatch",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["Operations"])
async def health(session: SessionDep) -> HealthResponse:
    await session.execute(select(1))
    return HealthResponse()


@app.post("/api/v1/auth/token", response_model=TokenResponse, tags=["Authentication"])
async def login(
    session: SessionDep,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    user = await session.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user.id, user.role))


@app.get("/api/v1/auth/me", response_model=UserRead, tags=["Authentication"])
async def me(user: CurrentUser) -> User:
    return user


@app.get("/api/v1/sites", response_model=list[SiteRead], tags=["Reference data"])
async def sites(session: SessionDep, _: CurrentUser) -> list[Site]:
    return list((await session.scalars(select(Site).order_by(Site.name))).all())


@app.get("/api/v1/technicians", response_model=list[UserRead], tags=["Reference data"])
async def technicians(session: SessionDep, _: CurrentUser) -> list[User]:
    query = (
        select(User)
        .where(
            User.role == UserRole.TECHNICIAN,
            User.is_active.is_(True),
        )
        .order_by(User.full_name)
    )
    return list((await session.scalars(query)).all())


def _apply_visibility(query: Any, user: User) -> Any:
    if UserRole(user.role) == UserRole.TECHNICIAN:
        return query.where(WorkOrder.assignee_id == user.id)
    return query


async def _count(session: Any, statement: Any) -> int:
    return int((await session.execute(statement)).scalar_one())


@app.get("/api/v1/dashboard", response_model=DashboardStats, tags=["Dashboard"])
async def dashboard(session: SessionDep, user: CurrentUser) -> DashboardStats:
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    visible: ColumnElement[bool]
    if UserRole(user.role) == UserRole.TECHNICIAN:
        visible = WorkOrder.assignee_id == user.id
    else:
        visible = true()
    active_values = [item.value for item in ACTIVE_STATUSES]

    active_orders = await _count(
        session,
        select(func.count())
        .select_from(WorkOrder)
        .where(visible, WorkOrder.status.in_(active_values)),
    )
    completed_today = await _count(
        session,
        select(func.count())
        .select_from(WorkOrder)
        .where(
            visible,
            WorkOrder.status == WorkOrderStatus.COMPLETED,
            WorkOrder.completed_at >= today,
        ),
    )
    overdue_orders = await _count(
        session,
        select(func.count())
        .select_from(WorkOrder)
        .where(
            visible,
            WorkOrder.status.in_(active_values),
            WorkOrder.sla_due_at < now,
        ),
    )
    technicians_on_duty = await _count(
        session,
        select(func.count())
        .select_from(User)
        .where(
            User.role == UserRole.TECHNICIAN,
            User.is_active.is_(True),
        ),
    )

    completed_total = await _count(
        session,
        select(func.count())
        .select_from(WorkOrder)
        .where(
            visible,
            WorkOrder.status == WorkOrderStatus.COMPLETED,
        ),
    )
    completed_in_sla = await _count(
        session,
        select(func.count())
        .select_from(WorkOrder)
        .where(
            visible,
            WorkOrder.status == WorkOrderStatus.COMPLETED,
            WorkOrder.completed_at <= WorkOrder.sla_due_at,
        ),
    )

    status_rows = (
        await session.execute(
            select(WorkOrder.status, func.count()).where(visible).group_by(WorkOrder.status)
        )
    ).all()
    priority_rows = (
        await session.execute(
            select(WorkOrder.priority, func.count()).where(visible).group_by(WorkOrder.priority)
        )
    ).all()
    upcoming_query = (
        _apply_visibility(work_order_query(), user)
        .where(WorkOrder.status.in_(active_values))
        .order_by(WorkOrder.scheduled_for)
        .limit(5)
    )
    upcoming = list((await session.scalars(upcoming_query)).all())

    return DashboardStats(
        active_orders=active_orders,
        completed_today=completed_today,
        technicians_on_duty=technicians_on_duty,
        overdue_orders=overdue_orders,
        sla_percent=round(completed_in_sla / completed_total * 100, 1)
        if completed_total
        else 100.0,
        status_counts={str(key): int(value) for key, value in status_rows},
        priority_counts={str(key): int(value) for key, value in priority_rows},
        upcoming=[to_work_order_read(order) for order in upcoming],
    )


@app.get("/api/v1/work-orders", response_model=WorkOrderPage, tags=["Work orders"])
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

    total = await _count(
        session,
        select(func.count()).select_from(WorkOrder).where(*filters),
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


@app.get("/api/v1/work-orders/{order_id}", response_model=WorkOrderRead, tags=["Work orders"])
async def get_work_order(order_id: UUID, session: SessionDep, user: CurrentUser) -> WorkOrderRead:
    order = await session.scalar(work_order_query().where(WorkOrder.id == order_id))
    if order is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    assert_technician_access(order, user)
    return to_work_order_read(order)


@app.post(
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
    if not replayed:
        await manager.broadcast(
            {"type": "work_order.created", "data": result.model_dump(mode="json")}
        )
    return result


@app.post(
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
    order = await locked_work_order(session, order_id)
    assert_version(order, payload.version)
    _, assignee = await validate_relations(session, order.site_id, payload.assignee_id)
    if assignee is None:
        raise HTTPException(status_code=422, detail="Assignee is required")
    previous_assignee = order.assignee_id
    previous_status = order.status
    order.assignee_id = assignee.id
    if WorkOrderStatus(order.status) == WorkOrderStatus.NEW:
        order.status = WorkOrderStatus.ASSIGNED
    order.version += 1
    session.add(
        WorkOrderEvent(
            work_order_id=order.id,
            actor_id=actor.id,
            event_type="assigned",
            from_status=previous_status,
            to_status=order.status,
            payload={
                "previous_assignee_id": str(previous_assignee) if previous_assignee else None,
                "assignee_id": str(assignee.id),
            },
        )
    )
    await session.commit()
    loaded = await session.scalar(work_order_query().where(WorkOrder.id == order.id))
    if loaded is None:
        raise HTTPException(status_code=500, detail="Updated work order was not found")
    result = to_work_order_read(loaded)
    await manager.broadcast({"type": "work_order.assigned", "data": result.model_dump(mode="json")})
    return result


@app.post(
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
    order = await locked_work_order(session, order_id)
    assert_technician_access(order, actor)
    assert_version(order, payload.version)
    if UserRole(actor.role) == UserRole.TECHNICIAN and payload.status == WorkOrderStatus.CANCELLED:
        raise HTTPException(status_code=403, detail="Technicians cannot cancel work orders")
    if payload.status == WorkOrderStatus.BLOCKED and not payload.comment:
        raise HTTPException(status_code=422, detail="A reason is required when blocking work")
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
            to_status=payload.status,
            comment=payload.comment,
        )
    )
    await session.commit()
    loaded = await session.scalar(work_order_query().where(WorkOrder.id == order.id))
    if loaded is None:
        raise HTTPException(status_code=500, detail="Updated work order was not found")
    result = to_work_order_read(loaded)
    await manager.broadcast({"type": "work_order.updated", "data": result.model_dump(mode="json")})
    return result


@app.get(
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


@app.websocket("/api/v1/realtime")
async def realtime(websocket: WebSocket, token: str = Query()) -> None:
    try:
        payload = decode_access_token(token)
        user_id = UUID(str(payload["sub"]))
        async with SessionFactory() as session:
            user = await session.scalar(
                select(User).where(User.id == user_id, User.is_active.is_(True))
            )
        if user is None:
            await websocket.close(code=4401)
            return
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        await websocket.close(code=4401)
        return

    await manager.connect(websocket)
    await websocket.send_json({"type": "connected", "user_id": str(user_id)})
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
