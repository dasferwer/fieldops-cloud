from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from fieldops.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


async def login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/api/v1/auth/token",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def preserve_journal() -> AsyncIterator[None]:
    """Retention-тесты восстанавливают журнал для последующего migration round-trip."""
    from sqlalchemy import delete, insert, select

    from fieldops.db import SessionFactory
    from fieldops.models import StreamEvent, StreamState

    async with SessionFactory() as session:
        head, floor = (
            await session.execute(select(StreamState.position, StreamState.retained_after))
        ).one()
        rows = (await session.execute(select(StreamEvent.__table__))).mappings().all()
        snapshot = [dict(row) for row in rows]
    try:
        yield
    finally:
        async with SessionFactory() as session, session.begin():
            state = (await session.scalars(select(StreamState).with_for_update())).one()
            await session.execute(delete(StreamEvent))
            if snapshot:
                await session.execute(insert(StreamEvent), snapshot)
            state.retained_after = floor
            state.position = head
