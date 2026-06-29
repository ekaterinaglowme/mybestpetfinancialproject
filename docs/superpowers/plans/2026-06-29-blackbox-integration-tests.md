# Чёрно-ящичные интеграционные тесты на user story — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Покрыть PetBank чёрно-ящичными интеграционными тестами по user story (US-1, US-2, остаток US-3, US-4), устойчивыми к смене бизнес-правил.

**Architecture:** Тесты бьют по реальному HTTP поднятого Docker-стека (Postgres + мок СтопЛиста + app из Dockerfile), ничего не импортируя из кода приложения. Стенд и первый тест US-3 (участие СтопЛиста) уже реализованы и зелёные — план достраивает остальное. Для US-4 (429/таймаут) поднимается отдельный «строгий» стенд с низким лимитом и коротким таймаутом.

**Tech Stack:** pytest, httpx (sync-клиент), docker compose, colima/docker, alembic (накат схемы с хоста), stdlib http.server (мок).

## Global Constraints

- Тесты НЕ проверяют конкретные бизнес-константы (пороги возраста 18–35, страна «китай», тексты причин) — только контракт ответа и факт интеграции. Где нужен конкретный исход как предусловие — берём его из живого ответа и `pytest.skip`, а не хардкодим.
- Каждый тест: сначала русский докстринг Дано/Когда/Тогда, потом код.
- Все тесты помечены `@pytest.mark.blackbox`. Каталог `tests_blackbox/` вне `testpaths` (обычный `pytest` остаётся быстрым и без Docker). Запуск: `pytest tests_blackbox/`.
- Клиент — `httpx` по живому `base_url`; никаких импортов из `app/src`.
- Коды ответов (verbatim из кода): rate-limit → `429`; request timeout → `503`; заём не найден → `404`; повторное погашение → `409`; ошибка валидации → `422`.
- Креды стенда: `petbank/petbank/petbank_test`. Основной стенд: app `:8000`, db `:5432`. Строгий стенд US-4: проект `petbank-blackbox-us4`, app `:8001`, db `:5433`.

---

### Task 1: US-1 — контракт ответа и валидация (`POST /applications`)

**Files:**
- Create: `tests_blackbox/test_us1_applications.py`

**Interfaces:**
- Consumes: фикстуру `base_url` (session-scope) из `tests_blackbox/conftest.py` → строка вида `http://localhost:8000`.
- Produces: ничего (листовой тест-файл).

- [ ] **Step 1: Написать тесты US-1**

```python
"""US-1. Подача заявки на кредит v1 — POST /applications.

Чёрный ящик: проверяем контракт ответа и валидацию, НЕ конкретные пороги
бизнес-правил (возраст/страна меняются и живут в юнит-тестах).
"""

import uuid

import httpx
import pytest


def _payload(**over) -> dict:
    base = {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        "phone": "+79991234567",
        "birth_date": "1995-05-15",
        "country": "Россия",
        "amount": 100000,
    }
    base.update(over)
    return base


@pytest.mark.blackbox
def test_otvet_sootvetstvuet_kontraktu(base_url):
    """Валидная заявка возвращает ответ заявленной формы.

    Дано: корректно заполненная заявка.
    Когда: POST /applications.
    Тогда: HTTP 200; в ответе есть application_id (валидный uuid), status из
           {approved, declined}, applicant с ФИО/возрастом/телефоном, reasons —
           список, received_at. ФИО и телефон совпадают с поданными. Какое именно
           решение — НЕ проверяем: это бизнес-правило.
    """
    p = _payload()
    r = httpx.post(f"{base_url}/applications", json=p, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    uuid.UUID(body["application_id"])  # валидный uuid — иначе ValueError
    assert body["status"] in {"approved", "declined"}
    assert isinstance(body["reasons"], list)
    assert body["received_at"]
    applicant = body["applicant"]
    assert applicant["full_name"] == "Иванов Иван Иванович"
    assert applicant["phone"] == p["phone"]
    assert isinstance(applicant["age"], int)


@pytest.mark.blackbox
@pytest.mark.parametrize(
    "bad, why",
    [
        ({"phone": None}, "нет обязательного телефона"),
        ({"birth_date": "2999-01-01"}, "дата рождения в будущем"),
        ({"birth_date": "15-05-1995"}, "неверный формат даты"),
    ],
)
def test_krivaya_zayavka_otvergaetsya_422(base_url, bad, why):
    """Некорректная заявка отклоняется с понятной ошибкой, а не «проваливается».

    Дано: заявка с дефектом (по очереди: нет телефона / дата в будущем /
          неверный формат даты).
    Когда: POST /applications.
    Тогда: HTTP 422 (контракт валидации; от бизнес-правил не зависит).
    """
    r = httpx.post(f"{base_url}/applications", json=_payload(**bad), timeout=10)
    assert r.status_code == 422, f"{why}: {r.status_code} {r.text}"
```

- [ ] **Step 2: Прогнать тесты**

Run: `pytest tests_blackbox/test_us1_applications.py -v` (Docker должен быть запущен; иначе тесты skip)
Expected: 4 passed (1 контракт + 3 параметра валидации). Стенд поднимается фикстурой `base_url`.

- [ ] **Step 3: Commit**

```bash
git add tests_blackbox/test_us1_applications.py
git commit -m "test(blackbox): US-1 контракт ответа и валидация /applications"
```

---

### Task 2: US-2 — жизненный цикл займа (`/loans/{id}`)

**Files:**
- Create: `tests_blackbox/test_us2_loans.py`

**Interfaces:**
- Consumes: фикстуру `base_url`.
- Produces: ничего.

- [ ] **Step 1: Написать тесты US-2**

```python
"""US-2. Жизненный цикл займа — /loans/{application_id}.

Чёрный ящик: проверяем инвариант «одобрение с суммой ⟺ заём существует» и
переходы статуса через реальную БД, без знания внутренней логики одобрения.
"""

import uuid

import httpx
import pytest


def _v1_payload() -> dict:
    return {
        "last_name": "Заёмщиков",
        "first_name": "Пётр",
        "middle_name": "",
        "phone": "+79995550011",
        "birth_date": "1995-03-20",
        "country": "Россия",
        "amount": 50000,
    }


@pytest.mark.blackbox
def test_invariant_odobrenie_s_summoy_sozdaet_zaem(base_url):
    """Заём существует тогда и только тогда, когда заявка одобрена с суммой.

    Дано: заявка с указанной суммой.
    Когда: POST /applications, затем GET /loans/{тот же id}.
    Тогда: если решение approved — заём есть (200), сумма совпадает, статус
           «не отдал»; если declined — займа нет (404). Конкретное решение не
           навязываем — проверяем согласованность БД с решением.
    """
    p = _v1_payload()
    created = httpx.post(f"{base_url}/applications", json=p, timeout=10)
    assert created.status_code == 200, created.text
    body = created.json()
    app_id = body["application_id"]

    loan = httpx.get(f"{base_url}/loans/{app_id}", timeout=10)
    if body["status"] == "approved":
        assert loan.status_code == 200, loan.text
        assert loan.json()["amount"] == p["amount"]
        assert loan.json()["status"] == "не отдал"
    else:
        assert loan.status_code == 404


@pytest.mark.blackbox
def test_zhiznennyy_cikl_pogasheniya(base_url):
    """Одобренный заём можно погасить один раз; повторно — нельзя.

    Дано: одобренная заявка с суммой (если текущие правила её не одобрили —
          тест пропускается, гасить нечего).
    Когда: GET статус → POST repay → POST repay ещё раз.
    Тогда: статус «не отдал» → после repay «отдал» (200) → повторный repay 409.
    """
    created = httpx.post(f"{base_url}/applications", json=_v1_payload(), timeout=10)
    assert created.status_code == 200, created.text
    body = created.json()
    if body["status"] != "approved":
        pytest.skip("текущие правила не одобрили заявку — гасить нечего")
    app_id = body["application_id"]

    status = httpx.get(f"{base_url}/loans/{app_id}", timeout=10)
    assert status.status_code == 200
    assert status.json()["status"] == "не отдал"

    repaid = httpx.post(f"{base_url}/loans/{app_id}/repay", timeout=10)
    assert repaid.status_code == 200, repaid.text
    assert repaid.json()["status"] == "отдал"
    assert repaid.json()["repaid_at"]

    again = httpx.post(f"{base_url}/loans/{app_id}/repay", timeout=10)
    assert again.status_code == 409


@pytest.mark.blackbox
def test_nesushchestvuyushchiy_zaem_404(base_url):
    """Запрос несуществующего займа — 404, а не 500.

    Дано: случайный uuid, под которым займа нет.
    Когда: GET /loans/{uuid} и POST /loans/{uuid}/repay.
    Тогда: оба → 404.
    """
    rid = str(uuid.uuid4())
    assert httpx.get(f"{base_url}/loans/{rid}", timeout=10).status_code == 404
    assert httpx.post(f"{base_url}/loans/{rid}/repay", timeout=10).status_code == 404
```

- [ ] **Step 2: Прогнать тесты**

Run: `pytest tests_blackbox/test_us2_loans.py -v`
Expected: 3 passed (lifecycle может быть skipped, если текущие правила не одобрили — это допустимо, не fail).

- [ ] **Step 3: Commit**

```bash
git add tests_blackbox/test_us2_loans.py
git commit -m "test(blackbox): US-2 жизненный цикл займа"
```

---

### Task 3: US-3 — fail-closed при недоступности СтопЛиста

**Files:**
- Modify: `tests_blackbox/blacklist_mock.py` (добавить «ошибочный» паспорт)
- Modify: `tests_blackbox/test_us3_applications_v2.py` (добавить тест fail-closed)

**Interfaces:**
- Consumes: фикстуру `base_url`; константу `ERROR_PASSPORT`.
- Produces: в моке паспорт `"5000000000"` → ответ HTTP 500 (имитация сбоя сервиса).

- [ ] **Step 1: Написать тест fail-closed (упадёт — мок ещё не умеет «ломаться»)**

Добавить в конец `tests_blackbox/test_us3_applications_v2.py`:

```python
# Паспорт, на котором мок СтопЛиста отвечает ошибкой 500 (имитация недоступности).
ERROR_PASSPORT = "5000000000"


@pytest.mark.blackbox
def test_stoplist_nedostupen_fail_closed(base_url):
    """СтопЛист недоступен → заявка не падает 5xx, а безопасно отклоняется.

    Дано: паспорт, на котором мок СтопЛиста возвращает ошибку (имитация сбоя).
    Когда: POST /applications/v2.
    Тогда: HTTP 200 (НЕ 5xx — ключевое свойство fail-closed) и решение declined.
           Сервис деградирует безопасно, а не ломается.
    """
    r = httpx.post(
        f"{base_url}/applications/v2", json=_v2_payload(ERROR_PASSPORT), timeout=10
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "declined"
```

- [ ] **Step 2: Прогнать — убедиться, что падает**

Run: `pytest "tests_blackbox/test_us3_applications_v2.py::test_stoplist_nedostupen_fail_closed" -v`
Expected: FAIL — мок пока считает `5000000000` чистым → решение `approved`, ассерт `declined` падает.

- [ ] **Step 3: Научить мок «ломаться» на ERROR_PASSPORT**

В `tests_blackbox/blacklist_mock.py` заменить блок констант и ветку `/check`:

```python
# Паспорт, который мок всегда считает «в чёрном списке».
BLACKLISTED = {"0000000000"}
# Паспорт, на котором мок имитирует сбой сервиса (HTTP 500).
ERROR_PASSPORTS = {"5000000000"}
```

и в `do_GET`, в ветке `if parsed.path == "/check":`, перед ответом:

```python
        if parsed.path == "/check":
            passport = parse_qs(parsed.query).get("passport", [""])[0]
            if passport in ERROR_PASSPORTS:
                self._json(500, {"error": "service unavailable"})
                return
            self._json(200, {"in_terror_list": passport in BLACKLISTED})
            return
```

- [ ] **Step 4: Прогнать весь US-3**

Run: `pytest tests_blackbox/test_us3_applications_v2.py -v`
Expected: 2 passed (участие СтопЛиста + fail-closed). Мок пересоздаётся при перезапуске стенда (volume read-only), новый код подхватится.

- [ ] **Step 5: Commit**

```bash
git add tests_blackbox/blacklist_mock.py tests_blackbox/test_us3_applications_v2.py
git commit -m "test(blackbox): US-3 fail-closed при недоступном СтопЛисте"
```

---

### Task 4: US-4 — строгий стенд (429 / таймаут) + health

**Files:**
- Create: `tests_blackbox/compose.blackbox.us4.yml`
- Modify: `tests_blackbox/conftest.py` (вынести подъём стенда в helper; добавить фикстуру `strict_base_url`)
- Modify: `tests_blackbox/blacklist_mock.py` (добавить «медленный» паспорт; перейти на ThreadingHTTPServer)
- Create: `tests_blackbox/test_us4_ops.py`

**Interfaces:**
- Consumes: фикстуры `base_url` и `strict_base_url`; константу `SLOW_PASSPORT`.
- Produces: фикстуру `strict_base_url` (session-scope) → `http://localhost:8001`; в моке паспорт `"9999999999"` → ответ с задержкой 2 c.

- [ ] **Step 1: Создать строгий compose-стенд**

`tests_blackbox/compose.blackbox.us4.yml`:

```yaml
# Строгий стенд для US-4: низкий rate-limit и короткий таймаут запроса.
# Отдельный проект и порты — может работать одновременно с основным стендом.
name: petbank-blackbox-us4

services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: petbank
      POSTGRES_PASSWORD: petbank
      POSTGRES_DB: petbank_test
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U petbank -d petbank_test"]
      interval: 2s
      timeout: 3s
      retries: 15

  blacklist:
    image: python:3.13-slim
    volumes:
      - ./blacklist_mock.py:/mock/blacklist_mock.py:ro
    command: python /mock/blacklist_mock.py
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health')"]
      interval: 2s
      timeout: 3s
      retries: 15

  app:
    build:
      context: ..
      dockerfile: Dockerfile
    environment:
      DB_HOST: db
      DB_PORT: "5432"
      DB_USER: petbank
      DB_PASSWORD: petbank
      DB_DATABASE: petbank_test
      BLACK_LIST_URL: http://blacklist:8090
      # Строгие пределы: маленький лимит и короткий таймаут запроса.
      RATE_LIMIT_RPS: "2"
      RATE_LIMIT_BURST: "2"
      REQUEST_TIMEOUT_SECONDS: "0.5"
      # Таймаут самого клиента СтопЛиста — БОЛЬШЕ задержки мока (2 c) и больше
      # REQUEST_TIMEOUT_SECONDS, чтобы первым сработал общий таймаут запроса.
      BLACK_LIST_TIMEOUT_SECONDS: "5"
    ports:
      - "8001:8000"
    depends_on:
      db:
        condition: service_healthy
      blacklist:
        condition: service_healthy
```

- [ ] **Step 2: Отрефакторить conftest и добавить strict_base_url**

Заменить `tests_blackbox/conftest.py` целиком:

```python
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
    _compose(compose_file, "up", "-d", "--build", "--wait", "app", "blacklist", check=True)
    _wait_health(f"{base_url}/health")

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
```

- [ ] **Step 3: Научить мок «медленному» паспорту и сделать его многопоточным**

В `tests_blackbox/blacklist_mock.py`:

заменить импорт сервера и добавить `time`:

```python
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
```

добавить константу рядом с другими:

```python
# Паспорт, на котором мок отвечает с большой задержкой (провоцирует таймаут запроса).
SLOW_PASSPORTS = {"9999999999"}
SLOW_DELAY_SECONDS = 2.0
```

в ветке `/check` добавить задержку перед ответом (после проверки ERROR_PASSPORTS):

```python
        if parsed.path == "/check":
            passport = parse_qs(parsed.query).get("passport", [""])[0]
            if passport in ERROR_PASSPORTS:
                self._json(500, {"error": "service unavailable"})
                return
            if passport in SLOW_PASSPORTS:
                time.sleep(SLOW_DELAY_SECONDS)
            self._json(200, {"in_terror_list": passport in BLACKLISTED})
            return
```

заменить запуск сервера на многопоточный (медленный запрос не блокирует остальные):

```python
if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
```

- [ ] **Step 4: Написать тесты US-4**

`tests_blackbox/test_us4_ops.py`:

```python
"""US-4. Эксплуатационные/сквозные сценарии.

429 и таймаут проверяются на отдельном «строгом» стенде (низкий лимит, короткий
таймаут) — фикстура strict_base_url.
"""

import httpx
import pytest

# Паспорт, на котором мок СтопЛиста отвечает с задержкой (см. blacklist_mock.py).
SLOW_PASSPORT = "9999999999"


@pytest.mark.blackbox
def test_health_zhiv(base_url):
    """Сервис жив.

    Когда: GET /health.
    Тогда: 200 и тело {"status": "ok"}.
    """
    r = httpx.get(f"{base_url}/health", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.blackbox
def test_rate_limit_otdaet_429(strict_base_url):
    """Под градом запросов часть отклоняется кодом 429.

    Дано: строгий стенд с низким лимитом RPS.
    Когда: быстро шлём много POST /applications подряд.
    Тогда: хотя бы один ответ — 429 (защита от перегрузки работает). Точное
           число не фиксируем — оно зависит от настроек, не от бизнес-правил.
    """
    payload = {
        "last_name": "Нагрузкин",
        "first_name": "Поток",
        "middle_name": "",
        "phone": "+79990000001",
        "birth_date": "1995-01-01",
        "country": "Россия",
        "amount": 1000,
    }
    codes = [
        httpx.post(f"{strict_base_url}/applications", json=payload, timeout=10).status_code
        for _ in range(20)
    ]
    assert 429 in codes, f"ни одного 429 среди {codes}"


@pytest.mark.blackbox
def test_dolgiy_zapros_obryvaetsya_503(strict_base_url):
    """Слишком долгий запрос обрывается предохранителем-таймаутом.

    Дано: строгий стенд с коротким REQUEST_TIMEOUT_SECONDS; паспорт, на котором
          мок СтопЛиста отвечает с большой задержкой.
    Когда: POST /applications/v2 с этим паспортом.
    Тогда: HTTP 503 (request timeout) — сервер не зависает на медленной зависимости.
    """
    payload = {
        "last_name": "Долгов",
        "first_name": "Тормоз",
        "middle_name": "",
        "phone": "+79990000002",
        "birth_date": "1995-01-01",
        "email": "slow@example.ru",
        "passport": SLOW_PASSPORT,
        "region": "Москва",
        "loan_purpose": "покупка",
        "amount": 1000,
    }
    r = httpx.post(f"{strict_base_url}/applications/v2", json=payload, timeout=10)
    assert r.status_code == 503, r.text
```

- [ ] **Step 5: Прогнать весь чёрный ящик**

Run: `pytest tests_blackbox/ -v`
Expected: все passed (US-1: 4, US-2: 3, US-3: 2, US-4: 3; lifecycle US-2 допустимо skipped). Поднимаются оба стенда — прогон дольше.

- [ ] **Step 6: Commit**

```bash
git add tests_blackbox/compose.blackbox.us4.yml tests_blackbox/conftest.py tests_blackbox/blacklist_mock.py tests_blackbox/test_us4_ops.py
git commit -m "test(blackbox): US-4 строгий стенд — rate-limit 429, таймаут 503, health"
```

---

## Self-Review

**Spec coverage:** US-1 → Task 1; US-2 → Task 2; US-3 (участие СтопЛиста — уже сделано; fail-closed) → Task 3; US-4 (health/429/таймаут) → Task 4. Сценарии US-3.13 (несовершеннолетний) намеренно опущены как чисто бизнес-правило (живёт в юнитах, неустойчиво). Все остальные пункты спеки покрыты.

**Placeholder scan:** плейсхолдеров нет — во всех шагах полный код и точные команды.

**Type consistency:** фикстуры `base_url`/`strict_base_url` возвращают строку URL; helper `_bring_up(compose_file, base_url, db_port)` единый для обеих; константы паспортов (`BLACKLISTED`/`ERROR_PASSPORTS`/`SLOW_PASSPORTS` в моке и `BLACKLISTED_PASSPORT`/`ERROR_PASSPORT`/`SLOW_PASSPORT` в тестах) согласованы по значениям.
