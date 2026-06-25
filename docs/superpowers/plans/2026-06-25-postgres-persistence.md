# PostgreSQL-персистентность заявок — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** При каждой заявке сохранять пользователя и заявку в две связанные таблицы PostgreSQL, не меняя контракт ответа ручки.

**Architecture:** Тонкий слой доступа к данным (`db.py` — подключение, `models.py` — ORM, `repository.py` — операции) поверх async SQLAlchemy. `make_decision` остаётся чистой функцией; сохранение навешивается в обработчике `POST /applications` через FastAPI-зависимость-сессию. Схема ведётся Alembic; тесты гоняются на in-memory SQLite.

**Tech Stack:** SQLAlchemy 2.x (async) · asyncpg · Alembic · pytest-asyncio · aiosqlite

**Спецификация:** [docs/superpowers/specs/2026-06-25-postgres-persistence-design.md](../specs/2026-06-25-postgres-persistence-design.md)

## Global Constraints

- Python **3.14** (как в `Dockerfile` и `ci.yml`).
- Новые runtime-зависимости: `sqlalchemy[asyncio]>=2,<3`, `asyncpg>=0.29`, `alembic>=1.13`.
- Новые dev-зависимости: `aiosqlite>=0.20`, `pytest-asyncio>=0.23`.
- DSN приложения: `postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_DATABASE}`.
- env-переменные: `DB_USER`, `DB_PASSWORD`, `DB_DATABASE` (обязательные), `DB_HOST` (default `localhost`), `DB_PORT` (default `5432`).
- Идентичность пользователя: `UNIQUE(last_name, first_name, middle_name, birth_date, phone)`, constraint `uq_user_identity`.
- `middle_name` — `NOT NULL DEFAULT ''`. `amount` — `Numeric(12, 2)` nullable. `reasons` — `JSON().with_variant(JSONB, "postgresql")`. Все `*_id` — `Uuid`. Все `*_at` — `DateTime(timezone=True)`.
- Сбой БД при заявке → **HTTP 500** (контракт успешного ответа неизменен).
- Прод: приложение ходит на `petbank-db:5432` (docker-сеть), миграция — на `212.147.238.3:5432`.
- Git: ветки от `main`, только rebase, без merge-коммитов ([CLAUDE.md](../../../CLAUDE.md)). Один коммит на задачу.
- `.env` / `.env.example` правит пользователь вручную (ассистенту доступ закрыт) — в плане помечено.

**Перед стартом:** вся работа — в отдельной ветке/worktree от `main` (см. superpowers:using-git-worktrees).

---

### Task 1: Слой подключения `db.py` + зависимости

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `class Base(DeclarativeBase)` — общий базовый класс моделей.
  - `build_dsn(env: Mapping[str, str] | None = None) -> str`
  - `configure(dsn: str, **engine_kwargs) -> None` — создаёт модульные `engine` и `AsyncSessionLocal`.
  - `async def dispose() -> None`
  - `async def get_session() -> AsyncIterator[AsyncSession]` — FastAPI-зависимость, коммит при успехе / откат при исключении.
  - module globals `engine: AsyncEngine | None`, `AsyncSessionLocal: async_sessionmaker | None`.

- [ ] **Step 1: Добавить зависимости**

В `requirements.txt` дописать:
```
sqlalchemy[asyncio]>=2,<3
asyncpg>=0.29
alembic>=1.13
```
В `requirements-dev.txt` дописать (после строки `httpx>=0.27,<1`):
```
aiosqlite>=0.20
pytest-asyncio>=0.23
```

- [ ] **Step 2: Установить зависимости**

Run: `pip install -r requirements-dev.txt`
Expected: успешная установка sqlalchemy, asyncpg, alembic, aiosqlite, pytest-asyncio.

- [ ] **Step 3: Написать падающий тест**

Create `tests/test_db.py`:
```python
from db import build_dsn


def test_build_dsn_from_env():
    env = {
        "DB_USER": "petbank",
        "DB_PASSWORD": "secret",
        "DB_HOST": "db.example",
        "DB_PORT": "5433",
        "DB_DATABASE": "petbank",
    }
    assert build_dsn(env) == "postgresql+asyncpg://petbank:secret@db.example:5433/petbank"


def test_build_dsn_defaults_host_and_port():
    env = {"DB_USER": "u", "DB_PASSWORD": "p", "DB_DATABASE": "d"}
    assert build_dsn(env) == "postgresql+asyncpg://u:p@localhost:5432/d"
```

- [ ] **Step 4: Запустить тест — должен упасть**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`.

- [ ] **Step 5: Реализовать `db.py`**

Create `db.py`:
```python
"""Слой подключения к PostgreSQL: DSN из env, async engine, сессия, lifespan-хелперы."""

import os
from collections.abc import AsyncIterator, Mapping

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""


def build_dsn(env: Mapping[str, str] | None = None) -> str:
    """Собрать async-DSN из переменных DB_* (по умолчанию — из окружения процесса)."""
    env = os.environ if env is None else env
    user = env["DB_USER"]
    password = env["DB_PASSWORD"]
    host = env.get("DB_HOST", "localhost")
    port = env.get("DB_PORT", "5432")
    database = env["DB_DATABASE"]
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


# Инициализируются в configure(): на старте приложения (lifespan) или в тестовой фикстуре.
engine: AsyncEngine | None = None
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def configure(dsn: str, **engine_kwargs) -> None:
    """Создать engine и фабрику сессий по DSN."""
    global engine, AsyncSessionLocal
    engine = create_async_engine(dsn, **engine_kwargs)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def dispose() -> None:
    """Закрыть engine и сбросить модульное состояние."""
    global engine, AsyncSessionLocal
    if engine is not None:
        await engine.dispose()
    engine = None
    AsyncSessionLocal = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: одна транзакция на запрос (commit при успехе, rollback при ошибке)."""
    assert AsyncSessionLocal is not None, "db.configure() не был вызван"
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

- [ ] **Step 6: Запустить тест — должен пройти**

Run: `pytest tests/test_db.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Коммит**

```bash
git add requirements.txt requirements-dev.txt db.py tests/test_db.py
git commit -m "feat: слой подключения к Postgres (db.py) и зависимости"
```

---

### Task 2: ORM-модели `models.py`

**Files:**
- Create: `models.py`
- Modify: `pyproject.toml` (добавить `asyncio_mode`)
- Create: `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `db.Base`, `db.configure`, `db.dispose`, `db.engine`.
- Produces:
  - `class User(Base)` — поля `id, last_name, first_name, middle_name, birth_date, phone, created_at`, связь `applications`, constraint `uq_user_identity`.
  - `class Application(Base)` — поля `application_id, user_id, amount, country, status, reasons, received_at, created_at`, связь `user`.
  - Фикстура `db_session` в `tests/conftest.py` → `AsyncSession` на in-memory SQLite со созданной схемой.

- [ ] **Step 1: Включить async-режим pytest**

В `pyproject.toml`, в секцию `[tool.pytest.ini_options]`, добавить строку:
```toml
asyncio_mode = "auto"
```

- [ ] **Step 2: Создать общую тестовую фикстуру БД**

Create `tests/conftest.py`:
```python
"""Общие тестовые фикстуры: in-memory SQLite вместо Postgres."""

import pytest_asyncio
from sqlalchemy.pool import StaticPool

import db
import models  # noqa: F401  — регистрирует таблицы в Base.metadata


@pytest_asyncio.fixture
async def db_setup():
    """Сконфигурировать db на единый in-memory SQLite и создать схему."""
    db.configure(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    await db.dispose()


@pytest_asyncio.fixture
async def db_session(db_setup):
    """Отдельная сессия для прямых тестов слоя данных."""
    async with db.AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 3: Написать падающий тест**

Create `tests/test_models.py`:
```python
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models import Application, User


async def test_user_application_relationship(db_session):
    user = User(
        last_name="Иванов", first_name="Иван", middle_name="",
        birth_date=date(1990, 5, 1), phone="+79990000000",
    )
    db_session.add(user)
    await db_session.flush()

    app = Application(
        application_id=uuid.uuid4(), user=user, amount=100000,
        country="Россия", status="approved", reasons=[],
        received_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(app)
    await db_session.flush()

    loaded = (await db_session.execute(select(Application))).scalar_one()
    assert loaded.user_id == user.id
    assert loaded.status == "approved"
    assert loaded.reasons == []


async def test_user_identity_unique(db_session):
    fields = dict(
        last_name="Петров", first_name="Пётр", middle_name="",
        birth_date=date(1995, 1, 1), phone="+79991112233",
    )
    db_session.add(User(**fields))
    await db_session.flush()
    db_session.add(User(**fields))
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

- [ ] **Step 4: Запустить тест — должен упасть**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'models'`.

- [ ] **Step 5: Реализовать `models.py`**

Create `models.py`:
```python
"""ORM-модели: пользователь и его заявки (связь 1:N)."""

import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Date, DateTime, Uuid

from db import Base

# JSONB на Postgres, обычный JSON/TEXT на SQLite (для тестов).
ReasonsType = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "last_name", "first_name", "middle_name", "birth_date", "phone",
            name="uq_user_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    last_name: Mapped[str] = mapped_column(String)
    first_name: Mapped[str] = mapped_column(String)
    middle_name: Mapped[str] = mapped_column(String, default="", server_default="")
    birth_date: Mapped[date] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    applications: Mapped[list["Application"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Application(Base):
    __tablename__ = "applications"

    application_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    country: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    reasons: Mapped[list[str]] = mapped_column(ReasonsType, default=list)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="applications")
```

- [ ] **Step 6: Запустить тест — должен пройти**

Run: `pytest tests/test_models.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Коммит**

```bash
git add models.py pyproject.toml tests/conftest.py tests/test_models.py
git commit -m "feat: ORM-модели User и Application со связью 1:N"
```

---

### Task 3: Репозиторий `repository.py`

**Files:**
- Create: `repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `models.User`, `models.Application`, фикстура `db_session`.
- Produces:
  - `async def get_or_create_user(session, *, last_name, first_name, middle_name, birth_date, phone) -> User`
  - `async def save_application(session, *, application_id, user, amount, country, status, reasons, received_at) -> Application`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_repository.py`:
```python
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from models import Application, User
from repository import get_or_create_user, save_application

IDENTITY = dict(
    last_name="Сидоров", first_name="Семён", middle_name="",
    birth_date=date(1992, 3, 3), phone="+79995554433",
)


async def test_get_or_create_reuses_existing_user(db_session):
    first = await get_or_create_user(db_session, **IDENTITY)
    await db_session.flush()
    second = await get_or_create_user(db_session, **IDENTITY)
    assert first.id == second.id
    count = (await db_session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 1


async def test_different_identity_creates_new_user(db_session):
    a = await get_or_create_user(db_session, **IDENTITY)
    b = await get_or_create_user(db_session, **{**IDENTITY, "phone": "+70000000000"})
    assert a.id != b.id


async def test_save_application_links_to_user(db_session):
    user = await get_or_create_user(db_session, **IDENTITY)
    await save_application(
        db_session, application_id=uuid.uuid4(), user=user, amount=50000,
        country="Россия", status="approved", reasons=[],
        received_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
    )
    app = (await db_session.execute(select(Application))).scalar_one()
    assert app.user_id == user.id
    assert app.status == "approved"
```

- [ ] **Step 2: Запустить тест — должен упасть**

Run: `pytest tests/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'repository'`.

- [ ] **Step 3: Реализовать `repository.py`**

Create `repository.py`:
```python
"""Операции с БД: найти-или-создать пользователя и сохранить заявку."""

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import Application, User


async def get_or_create_user(
    session: AsyncSession,
    *,
    last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date,
    phone: str,
) -> User:
    """Вернуть пользователя по связке ФИО+ДР+телефон или создать нового."""
    stmt = select(User).where(
        User.last_name == last_name,
        User.first_name == first_name,
        User.middle_name == middle_name,
        User.birth_date == birth_date,
        User.phone == phone,
    )
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        last_name=last_name, first_name=first_name, middle_name=middle_name,
        birth_date=birth_date, phone=phone,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        # Параллельный запрос успел создать того же пользователя — берём существующего.
        await session.rollback()
        user = (await session.execute(stmt)).scalar_one()
    return user


async def save_application(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    user: User,
    amount: float | None,
    country: str,
    status: str,
    reasons: list[str],
    received_at: datetime,
) -> Application:
    """Создать заявку, привязанную к пользователю."""
    application = Application(
        application_id=application_id, user=user, amount=amount,
        country=country, status=status, reasons=reasons, received_at=received_at,
    )
    session.add(application)
    await session.flush()
    return application
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `pytest tests/test_repository.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Коммит**

```bash
git add repository.py tests/test_repository.py
git commit -m "feat: репозиторий get_or_create_user и save_application"
```

---

### Task 4: Локальная БД — docker-compose env + `.env.example`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example` (**вручную пользователем** — ассистент даёт содержимое)

**Interfaces:**
- Produces: поднимаемый локально Postgres на `localhost:5432` с кредами из `DB_*`.

- [ ] **Step 1: Прокинуть DB_* в образ postgres**

Заменить `docker-compose.yml` на:
```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_DATABASE}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

- [ ] **Step 2: Пользователь заполняет `.env` и `.env.example`**

⚠️ Этот шаг выполняет **пользователь** (доступ ассистента к `.env*` закрыт). Содержимое `.env.example`:
```dotenv
DB_USER=petbank
DB_PASSWORD=change-me
DB_DATABASE=petbank
DB_HOST=localhost
DB_PORT=5432
```
Локальный `.env` — копия с реальным паролем.

- [ ] **Step 3: Проверить, что БД поднимается**

Run:
```bash
docker compose up -d db
docker compose exec db pg_isready -U "$DB_USER"
```
Expected: `accepting connections`.

- [ ] **Step 4: Коммит**

```bash
git add docker-compose.yml .env.example
git commit -m "chore: docker-compose берёт креды БД из DB_* переменных"
```
> Если `.env.example` ещё не обновлён пользователем — закоммитить только `docker-compose.yml`.

---

### Task 5: Alembic — инициализация и первая миграция

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`

**Interfaces:**
- Consumes: `db.build_dsn`, `db.Base`, `models` (для `target_metadata`).
- Produces: команда `alembic upgrade head` создаёт таблицы `users`, `applications`.

- [ ] **Step 1: Сгенерировать каркас Alembic (async)**

Run: `alembic init -t async alembic`
Expected: созданы `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, пустой `alembic/versions/`.

- [ ] **Step 2: Настроить `alembic/env.py`**

Заменить тело `alembic/env.py` на:
```python
import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from alembic import context

import db
import models  # noqa: F401  — регистрирует таблицы в Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", db.build_dsn())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.Base.metadata


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
```

- [ ] **Step 3: Написать первую миграцию вручную**

Create `alembic/versions/0001_initial.py`:
```python
"""initial: users + applications

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-25
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("middle_name", sa.String(), nullable=False, server_default=""),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "last_name", "first_name", "middle_name", "birth_date", "phone",
            name="uq_user_identity",
        ),
    )
    op.create_table(
        "applications",
        sa.Column("application_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("country", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "reasons", postgresql.JSONB(), nullable=False, server_default="[]",
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("applications")
    op.drop_table("users")
```

- [ ] **Step 4: Накатить миграцию на локальную БД**

Run (БД из Task 4 должна быть поднята, переменные `DB_*` — в окружении):
```bash
docker compose up -d db
alembic upgrade head
docker compose exec db psql -U "$DB_USER" -d "$DB_DATABASE" -c "\dt"
```
Expected: в выводе `\dt` присутствуют таблицы `users` и `applications`.

- [ ] **Step 5: Проверить откат**

Run:
```bash
alembic downgrade base
docker compose exec db psql -U "$DB_USER" -d "$DB_DATABASE" -c "\dt"
alembic upgrade head
```
Expected: после `downgrade base` таблиц нет (`Did not find any relations`), после повторного `upgrade head` — снова есть.

- [ ] **Step 6: Коммит**

```bash
git add alembic.ini alembic/
git commit -m "feat: Alembic (async) и первая миграция схемы users/applications"
```

---

### Task 6: Интеграция сохранения в `POST /applications`

**Files:**
- Modify: `server.py` (импорты, `lifespan`, `create_application`)
- Modify: `tests/conftest.py` (добавить `async_client`-фикстуру)
- Modify: `tests/test_http.py` (перевести на async + `async_client`)
- Test: `tests/test_persistence.py`

**Interfaces:**
- Consumes: `db.configure`, `db.build_dsn`, `db.dispose`, `db.get_session`, `db.AsyncSessionLocal`, `db.engine`, `db.Base`, `repository.get_or_create_user`, `repository.save_application`.
- Produces: `app` с lifespan; `async_client`-фикстура (httpx) для HTTP-тестов на SQLite.

> Почему httpx+ASGITransport, а не `TestClient`: тесты, читающие БД, должны быть
> async и жить в одном event loop с движком (in-memory SQLite на `StaticPool`
> держит одно соединение, привязанное к циклу). Синхронный `TestClient`,
> вызванный из async-теста, создал бы вложенный цикл и конфликт соединения.

- [ ] **Step 1: Добавить async HTTP-клиент в conftest**

В `tests/conftest.py` добавить в конец:
```python
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def async_client(db_setup):
    """HTTP-клиент поверх ASGI-приложения; БД уже сконфигурирована фикстурой на SQLite."""
    from server import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```
> `db_setup` уже задал `db.AsyncSessionLocal` на ту же in-memory БД, что увидит
> ручка. Lifespan приложения при `ASGITransport` не запускается — это намеренно,
> конфигурацию БД целиком берёт на себя фикстура.

- [ ] **Step 2: Перевести `tests/test_http.py` на async**

Заменить весь `tests/test_http.py` на (поведение ответов не меняется — это рефакторинг под async-клиент):
```python
"""Интеграционные тесты HTTP-слоя (httpx + ASGITransport, БД — in-memory SQLite)."""

from datetime import date


def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "country": "Россия",
        "birth_date": born.isoformat(),
    }


async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_root_help(async_client):
    resp = await async_client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "PetBank"


async def test_application_approved(async_client):
    resp = await async_client.post("/applications", json=_adult_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["applicant"]["age"] == 30
    assert body["reasons"] == []


async def test_application_declined_minor(async_client):
    payload = _adult_payload()
    payload["birth_date"] = date.today().replace(year=date.today().year - 10).isoformat()
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert body["reasons"]


async def test_application_declined_blocked_country(async_client):
    payload = _adult_payload()
    payload["country"] = "Китай"
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Китай" in reason for reason in body["reasons"])


async def test_application_validation_error(async_client):
    resp = await async_client.post("/applications", json={"first_name": "Иван"})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "last_name" in fields_with_errors


async def test_application_invalid_json(async_client):
    resp = await async_client.post(
        "/applications",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


async def test_unknown_path_404(async_client):
    resp = await async_client.get("/nope")
    assert resp.status_code == 404
```

- [ ] **Step 3: Запустить HTTP-тесты — должны пройти**

Run: `pytest tests/test_http.py -v`
Expected: PASS. Старый обработчик ещё не ходит в БД, но `async_client` корректно
прогоняет ASGI-приложение; контракт ответов не изменился.

- [ ] **Step 4: Написать падающие тесты персистентности**

Create `tests/test_persistence.py`:
```python
from datetime import date

from sqlalchemy import func, select

import db
from models import Application, User


def _payload(phone="+79991234567"):
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов", "first_name": "Иван", "phone": phone,
        "country": "Россия", "birth_date": born.isoformat(), "amount": 100000,
    }


async def _count(model):
    async with db.AsyncSessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_application_persisted(async_client):
    resp = await async_client.post("/applications", json=_payload())
    assert resp.status_code == 200
    assert await _count(Application) == 1


async def test_repeat_same_identity_reuses_user(async_client):
    await async_client.post("/applications", json=_payload())
    await async_client.post("/applications", json=_payload())
    assert await _count(User) == 1
    assert await _count(Application) == 2


async def test_different_identity_creates_two_users(async_client):
    await async_client.post("/applications", json=_payload(phone="+79991234567"))
    await async_client.post("/applications", json=_payload(phone="+70000000000"))
    assert await _count(User) == 2


async def test_db_failure_returns_500(async_client):
    # Дропаем таблицы — следующий запрос к БД упадёт, ручка должна вернуть 500.
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
    resp = await async_client.post("/applications", json=_payload(phone="+79993334455"))
    assert resp.status_code == 500
```

- [ ] **Step 5: Запустить — должны упасть**

Run: `pytest tests/test_persistence.py -v`
Expected: FAIL — `_count(Application) == 1` не выполняется (текущий обработчик ничего не пишет), `test_db_failure_returns_500` тоже падает (ручка возвращает 200).

- [ ] **Step 6: Реализовать интеграцию в `server.py`**

Добавить импорты вверху `server.py`:
```python
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db import get_session
from repository import get_or_create_user, save_application
```
Заменить создание приложения (`app = FastAPI(title="PetBank")`) на:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    own = db.AsyncSessionLocal is None       # в тестах уже сконфигурировано на SQLite
    if own:
        db.configure(db.build_dsn())
    yield
    if own:
        await db.dispose()


app = FastAPI(title="PetBank", lifespan=lifespan)
```
Заменить обработчик `create_application` на:
```python
@app.post("/applications", response_model=ApplicationDecision)
async def create_application(
    payload: ApplicationRequest,
    session: AsyncSession = Depends(get_session),
):
    decision = make_decision(payload)
    try:
        user = await get_or_create_user(
            session,
            last_name=payload.last_name,
            first_name=payload.first_name,
            middle_name=payload.middle_name,
            birth_date=payload.birth_date,
            phone=payload.phone,
        )
        await save_application(
            session,
            application_id=uuid.UUID(decision["application_id"]),
            user=user,
            amount=payload.amount,
            country=payload.country,
            status=decision["status"],
            reasons=decision["reasons"],
            received_at=datetime.fromisoformat(decision["received_at"]),
        )
    except SQLAlchemyError as exc:
        logger.exception("Не удалось сохранить заявку %s", decision["application_id"])
        raise HTTPException(status_code=500, detail="Ошибка сохранения заявки") from exc
    return decision
```
> `uuid` и `datetime` уже импортированы вверху `server.py` (`import uuid`, `from datetime import date, datetime`).

- [ ] **Step 7: Запустить весь сьют — должен пройти**

Run: `pytest -q`
Expected: PASS — `test_db`, `test_models`, `test_repository`, `test_http`, `test_persistence`, `test_decision`.

- [ ] **Step 8: Ручная проверка против реального Postgres**

Run (БД поднята, схема накатана из Task 5):
```bash
docker compose up -d db && alembic upgrade head
python main.py &
sleep 2
curl -s -X POST localhost:8000/applications -H 'Content-Type: application/json' \
  -d '{"last_name":"Иванов","first_name":"Иван","phone":"+79991234567","country":"Россия","birth_date":"1996-05-15","amount":100000}'
docker compose exec db psql -U "$DB_USER" -d "$DB_DATABASE" \
  -c "SELECT u.last_name, a.status FROM applications a JOIN users u ON u.id=a.user_id;"
kill %1
```
Expected: JSON-ответ со `status`, и строка `Иванов | approved` в выводе psql.

- [ ] **Step 9: Коммит**

```bash
git add server.py tests/conftest.py tests/test_http.py tests/test_persistence.py
git commit -m "feat: POST /applications сохраняет пользователя и заявку в Postgres"
```

- [ ] **Step 5: Запустить весь тест-сьют**

Run: `pytest -q`
Expected: PASS — все тесты (`test_db`, `test_models`, `test_repository`, `test_http`, `test_persistence`, `test_decision`).

- [ ] **Step 6: Ручная проверка против реального Postgres**

Run (БД поднята, схема накатана из Task 5):
```bash
docker compose up -d db && alembic upgrade head
python main.py &
sleep 2
curl -s -X POST localhost:8000/applications -H 'Content-Type: application/json' \
  -d '{"last_name":"Иванов","first_name":"Иван","phone":"+79991234567","country":"Россия","birth_date":"1996-05-15","amount":100000}'
docker compose exec db psql -U "$DB_USER" -d "$DB_DATABASE" \
  -c "SELECT u.last_name, a.status FROM applications a JOIN users u ON u.id=a.user_id;"
kill %1
```
Expected: JSON-ответ со `status`, и строка `Иванов | approved` в выводе psql.

- [ ] **Step 7: Коммит**

```bash
git add server.py tests/conftest.py tests/test_http.py tests/test_persistence.py
git commit -m "feat: POST /applications сохраняет пользователя и заявку в Postgres"
```

---

### Task 7: Dockerfile — добавить новые модули в образ

**Files:**
- Modify: `Dockerfile:17`

**Interfaces:**
- Consumes: `db.py`, `models.py`, `repository.py`.

- [ ] **Step 1: Скопировать новые модули в образ**

В `Dockerfile` заменить строку:
```dockerfile
COPY server.py main.py ./
```
на:
```dockerfile
COPY server.py main.py db.py models.py repository.py ./
```

- [ ] **Step 2: Проверить сборку образа**

Run: `docker build -t petbank:test .`
Expected: образ собирается без ошибок (`naming to docker.io/library/petbank:test`).

- [ ] **Step 3: Коммит**

```bash
git add Dockerfile
git commit -m "chore: добавить модули БД в Docker-образ"
```

---

### Task 8: CI — прод-сеть в деплое + ручная миграционная job

**Files:**
- Modify: `.github/workflows/ci.yml` (job `deploy`, шаг запуска контейнера)
- Create: `.github/workflows/migrate.yml`

**Interfaces:**
- Consumes: секреты `DB_USER`, `DB_PASSWORD`, `DB_DATABASE`; vars `SSH_HOST`, `SSH_USER`; secret `SSH_KEY`.

- [ ] **Step 1: Запускать приложение в docker-сети с env БД**

В `.github/workflows/ci.yml`, в job `deploy`, шаге «Pull image on VM and (re)start container», заменить строку `docker run`:
```bash
&& docker run -d --restart unless-stopped --name petbank -p 8000:8000 '${IMAGE}' \
```
на (сеть + проброс DB_*; сеть создаётся идемпотентно):
```bash
&& docker network create petbank-net 2>/dev/null || true \
&& docker run -d --restart unless-stopped --name petbank \
     --network petbank-net -p 8000:8000 \
     -e DB_HOST=petbank-db -e DB_PORT=5432 \
     -e DB_USER='${{ secrets.DB_USER }}' \
     -e DB_PASSWORD='${{ secrets.DB_PASSWORD }}' \
     -e DB_DATABASE='${{ secrets.DB_DATABASE }}' \
     '${IMAGE}' \
```
> Контейнер `petbank-db` (postgres:16 в сети `petbank-net`, том для данных) поднимается на VM **один раз вручную** — это разовая инфраструктурная операция, не часть пайплайна. Команда для VM:
> ```bash
> docker network create petbank-net 2>/dev/null || true
> docker run -d --name petbank-db --network petbank-net --restart unless-stopped \
>   -e POSTGRES_USER=<DB_USER> -e POSTGRES_PASSWORD=<DB_PASSWORD> -e POSTGRES_DB=<DB_DATABASE> \
>   -p 5432:5432 -v petbank_pgdata:/var/lib/postgresql/data postgres:16
> ```
> ⚠️ `-p 5432:5432` открывает порт наружу для миграционной job — обязательно ограничить firewall’ом VM (только диапазоны GitHub Actions). См. спецификацию §12.

- [ ] **Step 2: Создать ручную миграционную job**

Create `.github/workflows/migrate.yml`:
```yaml
name: DB migrate (manual)

# Запускается вручную кнопкой «Run workflow» в GitHub Actions.
on:
  workflow_dispatch:

jobs:
  migrate:
    name: alembic upgrade head
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run migrations
        env:
          DB_HOST: 212.147.238.3
          DB_PORT: "5432"
          DB_USER: ${{ secrets.DB_USER }}
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
          DB_DATABASE: ${{ secrets.DB_DATABASE }}
        run: alembic upgrade head
```

- [ ] **Step 3: Проверить синтаксис workflow**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/migrate.yml')); yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 4: Коммит**

```bash
git add .github/workflows/ci.yml .github/workflows/migrate.yml
git commit -m "ci: docker-сеть с БД в деплое и ручная миграционная job"
```

---

## Финальная проверка (после всех задач)

- [ ] `pytest -q` — весь сьют зелёный.
- [ ] Локально: `docker compose up -d db && alembic upgrade head && python main.py` — заявка сохраняется (Task 6 Step 6).
- [ ] `docker build -t petbank:test .` — образ собирается.
- [ ] Прод-чеклист (вне репозитория, выполняет пользователь): поднять `petbank-db` на VM, настроить секреты `DB_*` в GitHub, настроить firewall на 5432, прогнать миграционную job, передеплоить приложение.

## Покрытие спецификации

| Раздел спека | Задача |
|---|---|
| §3 Схема БД | Task 2, Task 5 |
| §4 Слой доступа | Task 1, Task 3 |
| §5 Изменения в ручке | Task 6 |
| §6 Конфигурация env | Task 1, Task 4 |
| §7 Alembic | Task 5 |
| §8 Локальная разработка | Task 4, Task 5 |
| §9 Тесты (SQLite) | Task 2, Task 3, Task 6 |
| §10 Прод-развёртывание | Task 7, Task 8 |
| §11 CI-миграция | Task 8 |
| §12 Безопасность | Task 8 (firewall-примечание) |
