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

from metrics import DB_TRANSACTION_SECONDS


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
            with DB_TRANSACTION_SECONDS.time():
                await session.commit()
        except Exception:
            await session.rollback()
            raise
