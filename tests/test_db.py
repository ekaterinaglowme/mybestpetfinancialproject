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
