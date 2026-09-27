"""Подписка читает общий журнал PostgreSQL и не зависит от процесса, принявшего запись."""

import asyncio
import logging
import time
from contextlib import suppress
from uuid import UUID

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from fieldops.config import get_settings
from fieldops.db import SessionFactory
from fieldops.models import StreamState, User, UserRole
from fieldops.security import decode_access_token
from fieldops.stream import read_batch

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: str = Field(min_length=1, max_length=4096)
    cursor: int | None = Field(default=None, ge=0, le=2**63 - 1)


async def send_frame(websocket: WebSocket, frame: dict[str, object]) -> None:
    # Медленный клиент не удерживает соединение с БД и не задерживает другие подписки.
    await asyncio.wait_for(websocket.send_json(frame), timeout=settings.realtime_send_timeout)


async def poll(websocket: WebSocket, user_id: UUID, role: str, expires: float, cursor: int) -> None:
    while True:
        if time.time() >= expires:
            await websocket.close(code=4401)
            return
        async with SessionFactory() as session:
            user = await session.get(User, user_id)
            if user is None or not user.is_active or user.role not in set(UserRole):
                await websocket.close(code=4401)
                return
            if user.role != role:
                await websocket.close(code=4403)
                return
            frame = await read_batch(session, user, cursor)
        await send_frame(websocket, frame)
        cursor = int(str(frame["cursor"]))
        if not frame.get("more"):
            await asyncio.sleep(min(settings.realtime_poll_seconds, max(0, expires - time.time())))


async def disconnect(websocket: WebSocket) -> None:
    # После первого сообщения приложение ничего не принимает; ping/pong обслуживает WebSocket.
    await websocket.receive_text()
    await websocket.close(code=4400)


@router.websocket("/api/v1/realtime")
async def realtime(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        await websocket.close(code=4403)
        return
    if websocket.query_params:
        await websocket.close(code=4400)
        return
    await websocket.accept()
    tasks: list[asyncio.Task[None]] = []
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw) > 8192:
            await websocket.close(code=4400)
            return
        subscription = Subscription.model_validate_json(raw)
        payload = decode_access_token(subscription.token)
        user_id = UUID(str(payload["sub"]))
        async with SessionFactory() as session:
            user = await session.get(User, user_id)
            if user is None or not user.is_active or user.role not in set(UserRole):
                await websocket.close(code=4401)
                return
            head = (await session.execute(select(StreamState.position))).scalar_one()
            role = user.role
        cursor = head if subscription.cursor is None else subscription.cursor
        await send_frame(
            websocket,
            {
                "type": "connected",
                "cursor": cursor,
                "reset": subscription.cursor is None,
            },
        )
        tasks = [
            asyncio.create_task(poll(websocket, user_id, role, float(payload["exp"]), cursor)),
            asyncio.create_task(disconnect(websocket)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (jwt.PyJWTError, KeyError, ValueError, TypeError, ValidationError):
        await websocket.close(code=4401)
    except TimeoutError:
        logger.info("realtime_timeout")
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=4408)
    except SQLAlchemyError:
        logger.warning("realtime_database_unavailable")
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1013)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
