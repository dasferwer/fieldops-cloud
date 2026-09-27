"""Проверяем обновление схемы только в изолированной тестовой БД."""

import asyncio
import hashlib
import subprocess

import asyncpg
from sqlalchemy.engine import make_url

from fieldops.config import get_settings

url = make_url(get_settings().database_url)
if url.database != "fieldops_test":
    raise SystemExit("Проверка миграции разрешена только для fieldops_test")


async def snapshot() -> str:
    connection = await asyncpg.connect(
        url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        digest = hashlib.sha256()
        for table in ("users", "sites", "work_orders", "work_order_events", "idempotency_records"):
            for row in await connection.fetch(
                f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"
            ):
                digest.update(row[0].encode())
        return digest.hexdigest()
    finally:
        await connection.close()


before = asyncio.run(snapshot())
subprocess.run(["alembic", "downgrade", "20260901_01"], check=True)
subprocess.run(["alembic", "upgrade", "head"], check=True)
assert asyncio.run(snapshot()) == before, "Миграция изменила предметные данные"
print("Миграция проверена: заявки, пользователи, аудит и ключи повторов сохранены")
