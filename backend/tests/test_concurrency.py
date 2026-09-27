import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from fieldops.db import SessionFactory
from fieldops.models import IdempotencyRecord, StreamEvent, StreamState, User, WorkOrder
from fieldops.stream import append_event, read_batch
from tests.conftest import auth
from tests.test_api import dispatcher_context, order_payload


async def test_concurrent_retries_create_one_order_and_event(client: AsyncClient) -> None:
    token, site, technician = await dispatcher_context(client)
    # Тело с датами фиксируем один раз: разные часы означают разные запросы.
    payload = order_payload(site, technician)
    key = str(uuid4())
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/work-orders", json=payload, headers={**auth(token), "Idempotency-Key": key}
            )
            for _ in range(12)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200] * 11 + [201]
    identity = UUID(responses[0].json()["id"])
    assert len({r.json()["id"] for r in responses}) == 1
    async with SessionFactory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(StreamEvent)
                .where(StreamEvent.work_order_id == identity)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.key == key)
            )
            == 1
        )


async def test_concurrent_transitions_have_one_winner(client: AsyncClient) -> None:
    token, site, technician = await dispatcher_context(client)
    response = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site, technician),
        headers={**auth(token), "Idempotency-Key": str(uuid4())},
    )
    order = response.json()
    responses = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/work-orders/{order['id']}/transition",
                json={"version": 1, "status": status},
                headers=auth(token),
            )
            for status in ["en_route", "cancelled"]
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_terminal_order_cannot_be_reassigned(client: AsyncClient) -> None:
    token, site, technician = await dispatcher_context(client)
    response = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site, technician),
        headers={**auth(token), "Idempotency-Key": str(uuid4())},
    )
    identity = response.json()["id"]
    await client.post(
        f"/api/v1/work-orders/{identity}/transition",
        json={"version": 1, "status": "cancelled"},
        headers=auth(token),
    )
    response = await client.post(
        f"/api/v1/work-orders/{identity}/assign",
        json={"version": 2, "assignee_id": technician},
        headers=auth(token),
    )
    assert response.status_code == 409


async def test_stream_failure_rolls_back_order(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, site, technician = await dispatcher_context(client)
    key = str(uuid4())

    async def fail(*args: object) -> None:
        raise RuntimeError("Проверка отката записи журнала")

    monkeypatch.setattr("fieldops.services.append_event", fail)
    async with SessionFactory() as session:
        before = await session.scalar(select(func.count()).select_from(WorkOrder))
    with pytest.raises(RuntimeError):
        await client.post(
            "/api/v1/work-orders",
            json=order_payload(site, technician),
            headers={**auth(token), "Idempotency-Key": key},
        )
    async with SessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(WorkOrder)) == before
        assert (
            await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == key))
            is None
        )


async def test_stream_cursor_cannot_pass_uncommitted_event(client: AsyncClient) -> None:
    token, site, technician = await dispatcher_context(client)
    created = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site, technician),
        headers={**auth(token), "Idempotency-Key": str(uuid4())},
    )
    identity = UUID(created.json()["id"])
    async with SessionFactory() as first, SessionFactory() as observer:
        order = await first.get(WorkOrder, identity)
        assert order is not None
        before = (await observer.execute(select(StreamState.position))).scalar_one()
        await append_event(first, order)
        assert (await observer.execute(select(StreamState.position))).scalar_one() == before
        await first.rollback()
        assert (await observer.execute(select(StreamState.position))).scalar_one() == before


async def test_bad_cursor_requests_resynchronization(client: AsyncClient) -> None:
    _, _, technician = await dispatcher_context(client)
    async with SessionFactory() as session:
        user = await session.get(User, UUID(technician))
        assert user is not None
        frame = await read_batch(session, user, 2**63 - 1)
        assert frame["type"] == "reset"
