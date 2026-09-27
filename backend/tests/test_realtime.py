import asyncio
import json
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from fieldops.db import SessionFactory
from fieldops.models import StreamState, User
from fieldops.realtime import send_frame
from tests.conftest import auth
from tests.test_api import dispatcher_context, order_payload


@pytest_asyncio.fixture(scope="session")
async def nodes() -> AsyncIterator[list[str]]:
    processes = []
    urls = []
    try:
        for _ in range(2):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "fieldops.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--ws-max-size",
                        "8192",
                        "--no-access-log",
                    ],
                    env={**os.environ, "FIELDOPS_REALTIME_POLL_SECONDS": "0.05"},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            url = f"http://127.0.0.1:{port}"
            async with AsyncClient() as http:
                for _attempt in range(100):
                    try:
                        if (await http.get(url + "/health")).status_code == 200:
                            break
                    except OSError:
                        pass
                    except Exception:
                        if processes[-1].poll() is not None:
                            raise AssertionError("API завершился до готовности") from None
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("API не запустился")
            urls.append(url)
        yield urls
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


async def subscribe(node: str, token: str, cursor: int | None = None) -> ClientConnection:
    websocket = await connect(node.replace("http:", "ws:") + "/api/v1/realtime")
    await websocket.send(json.dumps({"token": token, "cursor": cursor}))
    connected = json.loads(await asyncio.wait_for(websocket.recv(), 2))
    assert connected["type"] == "connected"
    return websocket


async def until_cursor(websocket: ClientConnection, cursor: int) -> list[dict[str, Any]]:
    events = []
    async with asyncio.timeout(5):
        while True:
            frame = json.loads(await websocket.recv())
            events.extend(frame.get("events", []))
            if frame["cursor"] >= cursor:
                return events


async def head() -> int:
    async with SessionFactory() as session:
        return (await session.execute(select(StreamState.position))).scalar_one()


async def test_two_nodes_replay_without_foreign_data(client: AsyncClient, nodes: list[str]) -> None:
    token, site, technician = await dispatcher_context(client)
    async with SessionFactory() as session:
        own = await session.get(User, UUID(technician))
        stranger = User(
            email=f"{uuid4()}@example.com",
            full_name="Другой техник",
            password_hash="unused",
            role="technician",
        )
        session.add(stranger)
        await session.commit()
        assert own is not None
        from fieldops.security import create_access_token

        own_token = create_access_token(own.id, own.role)
        foreign_token = create_access_token(stranger.id, stranger.role)
    cursor = await head()
    own_ws = await subscribe(nodes[1], own_token, cursor)
    foreign_ws = await subscribe(nodes[1], foreign_token, cursor)
    try:
        async with AsyncClient(base_url=nodes[0]) as writer:
            created = await writer.post(
                "/api/v1/work-orders",
                json=order_payload(site, technician),
                headers={**auth(token), "Idempotency-Key": str(uuid4())},
            )
        assert created.status_code == 201
        expected = {"type": "work_order.changed", "id": created.json()["id"]}
        position = await head()
        assert await until_cursor(own_ws, position) == [expected]
        assert await until_cursor(foreign_ws, position) == []
    finally:
        await own_ws.close()
        await foreign_ws.close()
    # Новое соединение на другом процессе восстанавливает событие после разрыва.
    replay = await subscribe(nodes[0], own_token, cursor)
    try:
        assert await until_cursor(replay, position) == [expected]
    finally:
        await replay.close()


async def test_reassignment_only_sends_removal_to_previous_owner(
    client: AsyncClient, nodes: list[str]
) -> None:
    token, site, technician = await dispatcher_context(client)
    from fieldops.security import create_access_token

    async with SessionFactory() as session:
        other = User(
            email=f"{uuid4()}@example.com",
            full_name="Новый исполнитель",
            password_hash="unused",
            role="technician",
        )
        session.add(other)
        await session.commit()
        other_id = str(other.id)
    own_token = create_access_token(UUID(technician), "technician")
    created = await client.post(
        "/api/v1/work-orders",
        json=order_payload(site, technician),
        headers={**auth(token), "Idempotency-Key": str(uuid4())},
    )
    cursor = await head()
    identity = created.json()["id"]
    response = await client.post(
        f"/api/v1/work-orders/{identity}/assign",
        json={"version": 1, "assignee_id": other_id},
        headers=auth(token),
    )
    assert response.status_code == 200
    websocket = await subscribe(nodes[1], own_token, cursor)
    try:
        assert await until_cursor(websocket, await head()) == [
            {"type": "work_order.removed", "id": identity}
        ]
        denied = await client.get(f"/api/v1/work-orders/{identity}", headers=auth(own_token))
        assert denied.status_code == 403
    finally:
        await websocket.close()


@pytest.mark.parametrize("change,code", [("inactive", 4401), ("role", 4403)])
async def test_active_subscription_loses_revoked_access(
    nodes: list[str], change: str, code: int
) -> None:
    from fieldops.security import create_access_token

    async with SessionFactory() as session:
        user = User(
            email=f"{uuid4()}@example.com",
            full_name="Временный доступ",
            password_hash="unused",
            role="dispatcher",
        )
        session.add(user)
        await session.commit()
        identity = user.id
    websocket = await subscribe(nodes[0], create_access_token(identity, "dispatcher"))
    try:
        async with SessionFactory() as session:
            user = await session.get(User, identity)
            assert user is not None
            if change == "inactive":
                user.is_active = False
            else:
                user.role = "technician"
            await session.commit()
        with pytest.raises(ConnectionClosed) as error:
            async with asyncio.timeout(3):
                while True:
                    await websocket.recv()
        assert error.value.rcvd is not None and error.value.rcvd.code == code
    finally:
        await websocket.close()


async def test_bad_origin_and_query_token_are_rejected(nodes: list[str]) -> None:
    url = nodes[0].replace("http:", "ws:") + "/api/v1/realtime"
    with pytest.raises(InvalidStatus):
        async with connect(url, origin="https://untrusted.invalid"):
            pass
    with pytest.raises(InvalidStatus):
        async with connect(url + "?token=secret"):
            pass


async def test_invalid_authentication_is_closed(nodes: list[str]) -> None:
    async with connect(nodes[0].replace("http:", "ws:") + "/api/v1/realtime") as websocket:
        await websocket.send(json.dumps({"token": "invalid"}))
        with pytest.raises(ConnectionClosed) as error:
            await websocket.recv()
        assert error.value.rcvd is not None and error.value.rcvd.code == 4401


async def test_slow_sender_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowSocket:
        async def send_json(self, value: object) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr("fieldops.realtime.settings.realtime_send_timeout", 0.05)
    with pytest.raises(TimeoutError):
        await send_frame(SlowSocket(), {})  # type: ignore[arg-type]


async def test_subscription_expires_even_without_new_events(nodes: list[str]) -> None:
    import time

    import jwt

    from fieldops.config import get_settings

    async with SessionFactory() as session:
        user = (await session.scalars(select(User).where(User.role == "dispatcher"))).first()
        assert user is not None
        identity = str(user.id)
    settings = get_settings()
    token = jwt.encode(
        {"sub": identity, "iat": time.time(), "exp": time.time() + 1.5},
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    websocket = await subscribe(nodes[0], token)
    try:
        with pytest.raises(ConnectionClosed) as error:
            async with asyncio.timeout(4):
                while True:
                    await websocket.recv()
        assert error.value.rcvd is not None and error.value.rcvd.code == 4401
    finally:
        await websocket.close()
