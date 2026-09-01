import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from fieldops.config import get_settings
from fieldops.db import SessionFactory
from fieldops.models import (
    Site,
    User,
    UserRole,
    WorkOrder,
    WorkOrderEvent,
    WorkOrderPriority,
    WorkOrderStatus,
)
from fieldops.security import hash_password


async def ensure_user(email: str, password: str, full_name: str, role: UserRole) -> User:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            user = User(
                email=email.lower(),
                full_name=full_name,
                password_hash=hash_password(password),
                role=role,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def run_seed() -> None:
    settings = get_settings()
    admin = await ensure_user(
        settings.admin_email,
        settings.admin_password.get_secret_value(),
        "FieldOps Administrator",
        UserRole.ADMIN,
    )
    dispatcher = await ensure_user(
        settings.dispatcher_email,
        settings.dispatcher_password.get_secret_value(),
        "Мария Орлова",
        UserRole.DISPATCHER,
    )
    technician = await ensure_user(
        settings.technician_email,
        settings.technician_password.get_secret_value(),
        "Алексей Морозов",
        UserRole.TECHNICIAN,
    )
    await ensure_user("irina@example.com", "ChangeMe123!", "Ирина Волкова", UserRole.TECHNICIAN)
    await ensure_user("denis@example.com", "ChangeMe123!", "Денис Ким", UserRole.TECHNICIAN)

    async with SessionFactory() as session:
        site_specs = [
            ("Склад Северный", "Москва, Дмитровское шоссе, 157", "Олег Серов", "+7 900 100-10-01"),
            (
                "БЦ Пульс",
                "Москва, Ленинградский проспект, 39",
                "Елена Лебедева",
                "+7 900 100-10-02",
            ),
            (
                "Терминал Восток",
                "Балашиха, Шоссе Энтузиастов, 12",
                "Антон Беляев",
                "+7 900 100-10-03",
            ),
        ]
        sites: list[Site] = []
        for name, address, contact_name, contact_phone in site_specs:
            site = await session.scalar(select(Site).where(Site.name == name))
            if site is None:
                site = Site(
                    name=name,
                    address=address,
                    contact_name=contact_name,
                    contact_phone=contact_phone,
                )
                session.add(site)
                await session.flush()
            sites.append(site)

        existing_orders = int((await session.scalar(select(WorkOrder).limit(1))) is not None)
        if not existing_orders:
            now = datetime.now(UTC)
            orders = [
                WorkOrder(
                    number="WO-DEMO-1048",
                    title="Диагностика линии упаковки",
                    description=(
                        "Линия останавливается при переходе на вторую скорость. "
                        "Проверить датчики и контроллер."
                    ),
                    site_id=sites[0].id,
                    assignee_id=technician.id,
                    created_by_id=dispatcher.id,
                    priority=WorkOrderPriority.CRITICAL,
                    status=WorkOrderStatus.IN_PROGRESS,
                    scheduled_for=now + timedelta(minutes=30),
                    sla_due_at=now + timedelta(hours=3),
                ),
                WorkOrder(
                    number="WO-DEMO-1051",
                    title="Плановое ТО вентиляции",
                    description=(
                        "Провести ежеквартальное обслуживание климатического "
                        "оборудования на 4 этаже."
                    ),
                    site_id=sites[1].id,
                    assignee_id=technician.id,
                    created_by_id=dispatcher.id,
                    priority=WorkOrderPriority.MEDIUM,
                    status=WorkOrderStatus.ASSIGNED,
                    scheduled_for=now + timedelta(hours=2),
                    sla_due_at=now + timedelta(hours=8),
                ),
                WorkOrder(
                    number="WO-DEMO-1055",
                    title="Замена контроллера доступа",
                    description=(
                        "Контроллер главного входа периодически теряет соединение с сервером."
                    ),
                    site_id=sites[2].id,
                    assignee_id=None,
                    created_by_id=admin.id,
                    priority=WorkOrderPriority.HIGH,
                    status=WorkOrderStatus.NEW,
                    scheduled_for=now + timedelta(hours=4),
                    sla_due_at=now + timedelta(hours=10),
                ),
            ]
            session.add_all(orders)
            await session.flush()
            session.add_all(
                [
                    WorkOrderEvent(
                        work_order_id=order.id,
                        actor_id=dispatcher.id,
                        event_type="created",
                        to_status=order.status,
                        payload={"seed": True},
                    )
                    for order in orders
                ]
            )
        await session.commit()


def main() -> None:
    asyncio.run(run_seed())


if __name__ == "__main__":
    main()
