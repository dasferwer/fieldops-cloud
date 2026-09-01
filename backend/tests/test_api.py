from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient

from tests.conftest import auth, login


async def dispatcher_context(client: AsyncClient) -> tuple[str, str, str]:
    token = await login(client, "dispatcher-test@example.com", "TestDispatcher123!")
    sites = (await client.get("/api/v1/sites", headers=auth(token))).json()
    technicians = (await client.get("/api/v1/technicians", headers=auth(token))).json()
    technician = next(
        item for item in technicians if item["email"] == "technician-test@example.com"
    )
    return token, str(sites[0]["id"]), str(technician["id"])


def order_payload(site_id: str, assignee_id: str | None = None) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "title": "Проверка резервного генератора",
        "description": "Выполнить диагностику запуска и проверить остаток топлива.",
        "site_id": site_id,
        "assignee_id": assignee_id,
        "priority": "high",
        "scheduled_for": (now + timedelta(hours=1)).isoformat(),
        "sla_due_at": (now + timedelta(hours=6)).isoformat(),
    }


async def test_health_auth_and_dashboard(client: AsyncClient) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "database": "ok"}

    token = await login(client, "dispatcher-test@example.com", "TestDispatcher123!")
    me = await client.get("/api/v1/auth/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["role"] == "dispatcher"

    dashboard = await client.get("/api/v1/dashboard", headers=auth(token))
    assert dashboard.status_code == 200
    assert dashboard.json()["technicians_on_duty"] >= 3
    assert dashboard.json()["active_orders"] >= 3


async def test_create_is_idempotent_and_rejects_changed_payload(client: AsyncClient) -> None:
    token, site_id, technician_id = await dispatcher_context(client)
    payload = order_payload(site_id, technician_id)
    key = f"test-{uuid4()}"
    headers = {**auth(token), "Idempotency-Key": key}

    created = await client.post("/api/v1/work-orders", json=payload, headers=headers)
    assert created.status_code == 201, created.text

    replay = await client.post("/api/v1/work-orders", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == created.json()["id"]

    changed = {**payload, "title": "Другая работа по тому же ключу"}
    conflict = await client.post("/api/v1/work-orders", json=changed, headers=headers)
    assert conflict.status_code == 409


async def test_state_machine_and_optimistic_lock(client: AsyncClient) -> None:
    token, site_id, technician_id = await dispatcher_context(client)
    created = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site_id, technician_id),
        headers={**auth(token), "Idempotency-Key": f"test-{uuid4()}"},
    )
    assert created.status_code == 201
    order = created.json()

    moved = await client.post(
        f"/api/v1/work-orders/{order['id']}/transition",
        json={"status": "en_route", "version": order["version"]},
        headers=auth(token),
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "en_route"

    stale = await client.post(
        f"/api/v1/work-orders/{order['id']}/transition",
        json={"status": "in_progress", "version": order["version"]},
        headers=auth(token),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == moved.json()["version"]

    invalid = await client.post(
        f"/api/v1/work-orders/{order['id']}/transition",
        json={"status": "completed", "version": moved.json()["version"]},
        headers=auth(token),
    )
    assert invalid.status_code == 409


async def test_technician_visibility_and_permissions(client: AsyncClient) -> None:
    dispatcher_token, site_id, technician_id = await dispatcher_context(client)
    created = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site_id, technician_id),
        headers={**auth(dispatcher_token), "Idempotency-Key": f"test-{uuid4()}"},
    )
    assert created.status_code == 201
    order = created.json()

    technician_token = await login(
        client,
        "technician-test@example.com",
        "TestTechnician123!",
    )
    page = await client.get("/api/v1/work-orders", headers=auth(technician_token))
    assert page.status_code == 200
    assert all(item["assignee_id"] == technician_id for item in page.json()["items"])

    cancel = await client.post(
        f"/api/v1/work-orders/{order['id']}/transition",
        json={"status": "cancelled", "version": order["version"]},
        headers=auth(technician_token),
    )
    assert cancel.status_code == 403


async def test_blocked_status_requires_reason(client: AsyncClient) -> None:
    token, site_id, technician_id = await dispatcher_context(client)
    created = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site_id, technician_id),
        headers={**auth(token), "Idempotency-Key": f"test-{uuid4()}"},
    )
    order = created.json()
    en_route = await client.post(
        f"/api/v1/work-orders/{order['id']}/transition",
        json={"status": "en_route", "version": order["version"]},
        headers=auth(token),
    )
    no_reason = await client.post(
        f"/api/v1/work-orders/{order['id']}/transition",
        json={"status": "blocked", "version": en_route.json()["version"]},
        headers=auth(token),
    )
    assert no_reason.status_code == 422
