"""Общие тестовые фикстуры: SQLite-файл вместо Postgres для всех тестов.

Файловый SQLite (а не in-memory) — чтобы и синхронный TestClient (origin-тесты
метрик/логов/ratelimit), и async httpx-клиент видели одну схему: каждый из них
открывает своё соединение к файлу в своём event loop. БД конфигурируется autouse,
т.к. ручка /applications теперь всегда ходит в базу.
"""

import pytest_asyncio

import db
import models  # noqa: F401 — регистрирует таблицы в Base.metadata


@pytest_asyncio.fixture(autouse=True)
async def db_setup(tmp_path):
    """Каждому тесту — свежий SQLite-файл со схемой."""
    db.configure(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    await db.dispose()


@pytest_asyncio.fixture
async def db_session(db_setup):
    """Отдельная сессия для прямых тестов слоя данных."""
    async with db.AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def async_client(db_setup):
    """HTTP-клиент поверх ASGI-приложения; БД уже сконфигурирована фикстурой на SQLite."""
    from httpx import ASGITransport, AsyncClient

    from server import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
