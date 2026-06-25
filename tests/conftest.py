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
