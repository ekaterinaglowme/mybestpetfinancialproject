"""Обвязка чёрно-ящичных тестов: поднимает Docker-стенд(ы) и отдаёт base_url.

Каталог вынесен из tests/ — чтобы автозапускаемая фикстура db_setup из
tests/conftest.py (она конфигурирует SQLite и импортирует код приложения) сюда
НЕ попадала. Чёрный ящик не знает о внутренностях: он только стучится по HTTP.

Запуск:  pytest tests_blackbox/        (нужен запущенный Docker)
"""

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent

COMPOSE_MAIN = HERE / "compose.blackbox.yml"
COMPOSE_US4 = HERE / "compose.blackbox.us4.yml"


def _compose(compose_file: Path, *args, **kwargs):
    return subprocess.run(
        ["docker", "compose", "-f", str(compose_file), *args], **kwargs
    )


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def _wait_health(url: str, timeout: float = 60.0) -> None:
    """Опрос служебной ручки до 200 с таймаутом — без «магических» sleep."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError as err:
            last_err = err
        time.sleep(1)
    raise RuntimeError(f"Сервис не поднялся за {timeout}s: {url} ({last_err})")


def _bring_up(compose_file: Path, base_url: str, db_port: str):
    """Поднять стенд, накатить схему, дождаться /health. Генератор для фикстуры."""
    if not _docker_available():
        pytest.skip("Docker недоступен — чёрно-ящичные тесты пропущены")

    # 1. Postgres (ждём healthy).
    _compose(compose_file, "up", "-d", "--wait", "db", check=True)

    # 2. Накат схемы с хоста — как на проде (миграции вне образа).
    #    alembic/env.py делает `import db, models` → нужен app/src в PYTHONPATH.
    alembic_env = {
        **os.environ,
        "DB_HOST": "localhost",
        "DB_PORT": db_port,
        "DB_USER": "petbank",
        "DB_PASSWORD": "petbank",
        "DB_DATABASE": "petbank_test",
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT / "app" / "src"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    subprocess.run(
        ["alembic", "upgrade", "head"], cwd=REPO_ROOT, env=alembic_env, check=True
    )

    # 3. Приложение + мок СтопЛиста (собрать образ, ждать healthy).
    #    Ждём /ready, а не /health: стенд «поднялся» = app доказал связность
    #    с БД, иначе первые тесты могут стартовать раньше готовности.
    _compose(compose_file, "up", "-d", "--build", "--wait", "app", "blacklist", check=True)
    _wait_health(f"{base_url}/ready")

    try:
        yield base_url
    finally:
        _compose(compose_file, "down", "-v")


@pytest.fixture(scope="session")
def base_url():
    """Основной стенд: app :8000, db :5432."""
    yield from _bring_up(COMPOSE_MAIN, "http://localhost:8000", "5432")


@pytest.fixture(scope="session")
def strict_base_url():
    """Строгий стенд для US-4: низкий лимит, короткий таймаут. app :8001, db :5433."""
    yield from _bring_up(COMPOSE_US4, "http://localhost:8001", "5433")
