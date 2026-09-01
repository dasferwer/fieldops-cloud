# FieldOps Cloud

Fullstack-платформа для диспетчеризации выездных работ: заявки, назначение
техников, контроль SLA, строгая машина состояний и обновления в реальном
времени. Проект показывает не только CRUD, а типовые проблемы рабочего B2B
продукта — права доступа, конкурентное редактирование, повторные запросы и
аудит действий.

> English overview: a production-shaped field-service operations platform
> built with FastAPI, PostgreSQL and React. It demonstrates RBAC, SLA tracking,
> idempotent writes, optimistic concurrency, audit history and WebSocket-driven
> UI refreshes.

## История проекта

- первоначальная разработка: июль — сентябрь 2025 года (период указан
  приблизительно);
- подготовка портфолио-версии: сентябрь 2026 года.

Репозиторий содержит актуализированную и документированную версию проекта,
подготовленную для публичного портфолио.

![FieldOps Cloud social preview](frontend/public/og.png)

## Что реализовано

- роли `admin`, `dispatcher`, `technician` и JWT-аутентификация;
- заявки с фильтрацией, пагинацией, назначением исполнителя и SLA;
- допустимые переходы `new → assigned → en_route → in_progress → completed`;
- блокировка некорректных переходов и обязательная причина для `blocked`;
- optimistic locking через поле `version` и ответ `409 Conflict`;
- идемпотентное создание по `Idempotency-Key` с защитой от смены payload;
- журнал событий каждой заявки;
- WebSocket-уведомления и инвалидация клиентского кеша;
- агрегаты dashboard по статусам, приоритетам и выполнению SLA;
- адаптивный интерфейс с формами React Hook Form + Zod;
- миграции Alembic, seed-данные, OpenAPI и интеграционные тесты.

## Стек

**Backend:** Python 3.13, FastAPI, SQLAlchemy 2 (async), PostgreSQL 17,
Alembic, Pydantic 2, PyJWT, Argon2, pytest, Ruff, mypy.

**Frontend:** React 19, TypeScript, Vinext, TanStack Query, React Hook Form,
Zod, shadcn/ui, Tailwind CSS.

**Infrastructure:** Docker Compose, multi-stage Dockerfiles, health checks.

## Быстрый запуск

Требуются Docker и Docker Compose.

```bash
cp .env.example .env
docker compose up --build -d
```

После прохождения health checks:

- интерфейс: <http://localhost:8060>
- Swagger UI: <http://localhost:8061/docs>
- health check: <http://localhost:8061/health>

Демонстрационные пользователи:

| Роль | Email | Пароль |
|---|---|---|
| Admin | `admin@example.com` | `ChangeMe123!` |
| Dispatcher | `dispatcher@example.com` | `ChangeMe123!` |
| Technician | `technician@example.com` | `ChangeMe123!` |

Значения предназначены только для локального запуска и меняются через `.env`.

Остановка:

```bash
docker compose down
```

## Проверки

Backend quality gate:

```bash
cd backend
uv sync --dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

Интеграционные тесты в изолированной PostgreSQL:

```bash
docker compose --profile test up --build \
  --abort-on-container-exit --exit-code-from test
docker compose --profile test down
```

Frontend quality gate:

```bash
cd frontend
npm ci
npm run format -- --check
npm run lint
npx tsc --noEmit
npm run build
```

## Архитектура и решения

Подробная схема, модель конкурентного обновления и границы системы описаны в
[docs/architecture.md](docs/architecture.md).

Основные API-маршруты:

| Метод | Маршрут | Назначение |
|---|---|---|
| `POST` | `/api/v1/auth/token` | OAuth2 password login |
| `GET` | `/api/v1/dashboard` | агрегаты с учётом роли |
| `GET/POST` | `/api/v1/work-orders` | поиск и создание заявок |
| `POST` | `/api/v1/work-orders/{id}/assign` | назначение техника |
| `POST` | `/api/v1/work-orders/{id}/transition` | переход состояния |
| `GET` | `/api/v1/work-orders/{id}/events` | аудит заявки |
| `WS` | `/api/v1/realtime?token=...` | события для интерфейса |

## Проверяемые сценарии отказа

- повтор создания с тем же ключом возвращает исходную заявку;
- тот же ключ с другим телом запроса возвращает `409`;
- устаревшая версия заявки возвращает `409` и актуальную версию;
- запрещённый переход состояния возвращает `409`;
- техник видит только назначенные ему заявки и не может отменить работу;
- переход в `blocked` без причины отклоняется валидацией.

## Repository map

```text
backend/                 FastAPI application and integration tests
  migrations/            Alembic migration history
  src/fieldops/           domain, API, auth, realtime and seed modules
frontend/                React operations dashboard
docs/architecture.md     design decisions and diagrams
docker-compose.yml       application and isolated test profiles
```

Проект является демонстрационным: внешняя отправка уведомлений и промышленное
хранение секретов оставлены за границами локальной версии.
