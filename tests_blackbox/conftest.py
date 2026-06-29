"""Обвязка чёрно-ящичных тестов: поднимает Docker-стенд и отдаёт base_url.

Этот каталог намеренно вынесен из tests/ — чтобы автозапускаемая фикстура
db_setup из tests/conftest.py (она конфигурирует SQLite и импортирует код
приложения) сюда НЕ попадала. Чёрный ящик не знает о внутренностях: он только
стучится по HTTP.

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
COMPOSE_FILE = HERE / "compose.blackbox.yml"

BASE_URL = "http://localhost:8000"

# Креды/порт стенда — синхронно с compose.blackbox.yml. Для alembic с хоста
# DB_HOST=localhost (порт Postgres проброшен наружу).
DB_ENV = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USER": "petbank",
    "DB_PASSWORD": "petbank",
    "DB_DATABASE": "petbank_test",
}


def _compose(*args, **kwargs):
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args], **kwargs
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
    """Опрос /health с таймаутом — без «магических» sleep."""
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


@pytest.fixture(scope="session")
def base_url():
    """Поднять стенд один раз на сессию, отдать base_url, в конце — снести."""
    if not _docker_available():
        pytest.skip("Docker недоступен — чёрно-ящичные тесты пропущены")

    # 1. Postgres (ждём healthy).
    _compose("up", "-d", "--wait", "db", check=True)

    # 2. Накат схемы с хоста — как на проде (миграции вне образа).
    #    alembic/env.py делает `import db, models` → нужен app/src в PYTHONPATH.
    alembic_env = {
        **os.environ,
        **DB_ENV,
        "PYTHONPATH": os.pathsep.join(
            [str(REPO_ROOT / "app" / "src"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    subprocess.run(
        ["alembic", "upgrade", "head"], cwd=REPO_ROOT, env=alembic_env, check=True
    )

    # 3. Приложение + мок СтопЛиста (собрать образ, ждать healthy).
    _compose("up", "-d", "--build", "--wait", "app", "blacklist", check=True)
    _wait_health(f"{BASE_URL}/health")

    try:
        yield BASE_URL
    finally:
        _compose("down", "-v")
