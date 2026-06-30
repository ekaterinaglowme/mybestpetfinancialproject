from db import build_dsn, engine_options


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


def test_engine_options_defaults():
    # По умолчанию: пул 20+10, ждать соединение из пула не дольше 5с,
    # один SQL-запрос — не дольше 5с, pre-ping проверяет живость соединения.
    opts = engine_options({})
    assert opts["pool_size"] == 20
    assert opts["max_overflow"] == 10
    assert opts["pool_timeout"] == 5.0
    assert opts["pool_pre_ping"] is True
    assert opts["connect_args"]["command_timeout"] == 5.0


def test_engine_options_overridable_from_env():
    env = {
        "DB_POOL_SIZE": "30",
        "DB_MAX_OVERFLOW": "5",
        "DB_POOL_TIMEOUT_SECONDS": "2.5",
        "DB_COMMAND_TIMEOUT_SECONDS": "3",
    }
    opts = engine_options(env)
    assert opts["pool_size"] == 30
    assert opts["max_overflow"] == 5
    assert opts["pool_timeout"] == 2.5
    assert opts["connect_args"]["command_timeout"] == 3.0


async def test_engine_options_reach_the_pool():
    # Параметры реально доходят до пула SQLAlchemy (а не молча игнорируются
    # из-за опечатки в имени kwarg). Движок не подключается, пока его не дёрнут.
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(
        "postgresql+asyncpg://u:p@localhost/d", **engine_options({})
    )
    try:
        assert eng.pool.timeout() == 5.0
        assert eng.pool.size() == 20
    finally:
        await eng.dispose()
