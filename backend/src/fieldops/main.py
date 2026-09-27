from datetime import UTC, datetime
from typing import Annotated, Any

from anyio import to_thread
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, true
from sqlalchemy.sql.elements import ColumnElement

from fieldops.config import get_settings
from fieldops.dependencies import CurrentUser, SessionDep
from fieldops.models import (
    Site,
    User,
    UserRole,
    WorkOrder,
    WorkOrderStatus,
)
from fieldops.observability import instrument
from fieldops.orders import router as orders_router
from fieldops.realtime import router as realtime_router
from fieldops.schemas import (
    DashboardStats,
    HealthResponse,
    SiteRead,
    TokenResponse,
    UserRead,
)
from fieldops.security import create_access_token, verify_password
from fieldops.services import (
    ACTIVE_STATUSES,
    to_work_order_read,
    work_order_query,
)

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    summary="Field service operations with SLA and real-time dispatch",
)
instrument(app)
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
    if (
        user is None
        or not user.is_active
        or not await to_thread.run_sync(verify_password, form.password, user.password_hash)
    ):
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
async def sites(session: SessionDep, user: CurrentUser) -> list[Site]:
    query = select(Site).order_by(Site.name)
    if user.role == UserRole.TECHNICIAN:
        query = query.where(
            Site.id.in_(select(WorkOrder.site_id).where(WorkOrder.assignee_id == user.id))
        )
    return list((await session.scalars(query)).all())


@app.get("/api/v1/technicians", response_model=list[UserRead], tags=["Reference data"])
async def technicians(session: SessionDep, user: CurrentUser) -> list[User]:
    query = (
        select(User)
        .where(
            User.role == UserRole.TECHNICIAN,
            User.is_active.is_(True),
        )
        .order_by(User.full_name)
    )
    if user.role == UserRole.TECHNICIAN:
        query = query.where(User.id == user.id)
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


app.include_router(orders_router)
app.include_router(realtime_router)
