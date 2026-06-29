"""Слой подключения к PostgreSQL: DSN из env, async engine, сессия, lifespan-хелперы."""

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from metrics import DB_TRANSACTION_SECONDS

# Сколько ждать ответ БД в readiness-пробе, прежде чем счесть её недоступной.
# Сама проба не должна висеть дольше — иначе readiness теряет смысл.
READY_TIMEOUT_SECONDS = float(os.environ.get("DB_READY_TIMEOUT_SECONDS", "2.0"))


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


def engine_options(env: Mapping[str, str] | None = None) -> dict:
    """Параметры боевого async-engine (Postgres/asyncpg): пул и таймауты.

    Принцип: любой I/O — с таймаутом, БД тоже. Иначе исчерпание пула или
    залипший запрос подвешивают воркер навсегда и копят очередь под нагрузкой.
      - pool_timeout      — сколько ждать свободное соединение из пула;
      - command_timeout   — предел на один SQL-запрос (asyncpg);
      - pool_pre_ping     — проверять живость соединения перед выдачей
                            (отсекает «мертвецов» после рестарта БД).
    Все значения переопределяются через env. Не передаётся для SQLite в тестах.
    """
    env = os.environ if env is None else env
    return {
        "pool_size": int(env.get("DB_POOL_SIZE", "20")),
        "max_overflow": int(env.get("DB_MAX_OVERFLOW", "10")),
        "pool_timeout": float(env.get("DB_POOL_TIMEOUT_SECONDS", "5")),
        "pool_pre_ping": True,
        "connect_args": {
            "command_timeout": float(env.get("DB_COMMAND_TIMEOUT_SECONDS", "5")),
        },
    }


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


async def check_ready() -> bool:
    """True, если БД отвечает на SELECT 1 за отведённый таймаут.

    Любой сбой (БД не сконфигурирована, недоступна, запрос завис) → False.
    Это readiness-сигнал: «готов ли инстанс принимать трафик», а не liveness.
    """
    if AsyncSessionLocal is None:
        return False
    try:
        async with asyncio.timeout(READY_TIMEOUT_SECONDS):
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncSession]:
    """То же, что get_session, но обычный контекст-менеджер (не FastAPI-зависимость).
    Берётся ВНУТРИ хендлера, чтобы не держать соединение из пула во время внешних
    вызовов (напр. чёрного списка) — иначе под залпом заявок пул копит очередь."""
    assert AsyncSessionLocal is not None, "db.configure() не был вызван"
    async with AsyncSessionLocal() as session:
        try:
            yield session
            with DB_TRANSACTION_SECONDS.time():
                await session.commit()
        except Exception:
            await session.rollback()
            raise
