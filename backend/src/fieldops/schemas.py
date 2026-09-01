from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from fieldops.models import UserRole, WorkOrderPriority, WorkOrderStatus


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    address: str
    contact_name: str
    contact_phone: str


class WorkOrderCreate(BaseModel):
    title: str = Field(min_length=4, max_length=220)
    description: str = Field(min_length=10, max_length=5000)
    site_id: UUID
    assignee_id: UUID | None = None
    priority: WorkOrderPriority = WorkOrderPriority.MEDIUM
    scheduled_for: datetime
    sla_due_at: datetime

    @field_validator("scheduled_for", "sla_due_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware datetime is required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_schedule(self) -> "WorkOrderCreate":
        if self.sla_due_at <= self.scheduled_for:
            raise ValueError("sla_due_at must be later than scheduled_for")
        return self


class WorkOrderAssign(BaseModel):
    assignee_id: UUID
    version: int = Field(ge=1)


class WorkOrderTransition(BaseModel):
    status: WorkOrderStatus
    version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=2000)


class WorkOrderRead(BaseModel):
    id: UUID
    number: str
    title: str
    description: str
    site_id: UUID
    site_name: str
    site_address: str
    assignee_id: UUID | None
    assignee_name: str | None
    priority: WorkOrderPriority
    status: WorkOrderStatus
    scheduled_for: datetime
    sla_due_at: datetime
    sla_breached: bool
    version: int
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkOrderPage(BaseModel):
    items: list[WorkOrderRead]
    total: int
    limit: int
    offset: int


class WorkOrderEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    from_status: WorkOrderStatus | None
    to_status: WorkOrderStatus | None
    comment: str | None
    actor_id: UUID
    occurred_at: datetime


class DashboardStats(BaseModel):
    active_orders: int
    completed_today: int
    technicians_on_duty: int
    overdue_orders: int
    sla_percent: float
    status_counts: dict[str, int]
    priority_counts: dict[str, int]
    upcoming: list[WorkOrderRead]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    database: Literal["ok"] = "ok"
