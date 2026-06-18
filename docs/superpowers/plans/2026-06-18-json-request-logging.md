# JSON-логирование и логирование HTTP-запросов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Все логи PetBank пишутся одной JSON-строкой на событие в stdout; каждый HTTP-запрос логируется с метаданными и полным телом; логи одного запроса связаны общим `request_id`.

**Architecture:** Новый модуль `logging_setup.py` (стандартная библиотека) даёт `JsonFormatter`, инъекцию `request_id` через `setLogRecordFactory` и идемпотентный `setup_logging()`. В `server.py` добавляется middleware `@app.middleware("http")`, которое кладёт `request_id` в `ContextVar`, читает тело и пишет access-лог. Существующие бизнес-логи в `make_decision` автоматически получают `request_id`.

**Tech Stack:** Python stdlib (`logging`, `json`, `contextvars`, `datetime`, `time`, `uuid`), FastAPI/Starlette middleware, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- Без новых runtime-зависимостей — только стандартная библиотека и middleware из состава FastAPI/Starlette.
- Логи идут в stdout, одна JSON-строка на событие; `json.dumps(..., ensure_ascii=False, default=str)` (кириллица не экранируется).
- Комментарии и docstring — на русском, в стиле существующего кода.
- Не пушить в `main` (push в main триггерит деплой). Работа ведётся в ветке `feat/json-request-logging`.
- Существующие тесты (`tests/test_decision.py`, `tests/test_http.py`) должны оставаться зелёными.

## Файловая структура

| Файл | Ответственность |
|---|---|
| `logging_setup.py` (новый) | Формат и настройка логирования: `JsonFormatter`, `request_id_ctx`, инъекция `request_id`, `setup_logging()` |
| `server.py` (правки) | Подключение `setup_logging()`, middleware логирования запросов, `extra` в логе решения, аргументы `uvicorn.run` |
| `tests/test_logging.py` (новый) | Тесты форматтера, middleware, тела, фоллбэка, корреляции, заголовка `X-Request-ID` |

---

### Task 1: Модуль `logging_setup.py` (формат + настройка)

**Files:**
- Create: `logging_setup.py`
- Test: `tests/test_logging.py`

**Interfaces:**
- Produces:
  - `request_id_ctx: contextvars.ContextVar[str]` (default `""`)
  - `class JsonFormatter(logging.Formatter)` — `format(record) -> str` возвращает JSON-строку
  - `def setup_logging(level: int = logging.INFO) -> None` — идемпотентно ставит JSON-хендлер на root + record factory

- [ ] **Step 1: Написать падающие тесты форматтера**

Создать `tests/test_logging.py`:

```python
"""Тесты JSON-логирования: форматтер, middleware, корреляция request_id."""

import json
import logging

from logging_setup import JsonFormatter


def _record(name="server", level=logging.INFO, msg="сообщение", **extra):
    record = logging.LogRecord(name, level, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_outputs_valid_json_with_base_fields():
    out = JsonFormatter().format(_record(msg="Заявка одобрена"))
    data = json.loads(out)
    assert data["level"] == "INFO"
    assert data["logger"] == "server"
    assert data["message"] == "Заявка одобрена"
    assert "timestamp" in data
    # Кириллица не экранируется
    assert "Заявка одобрена" in out
    # request_id пуст → поле опущено
    assert "request_id" not in data


def test_formatter_includes_extra_fields():
    out = JsonFormatter().format(
        _record(name="petbank.access", event="http_request", status_code=200)
    )
    data = json.loads(out)
    assert data["event"] == "http_request"
    assert data["status_code"] == 200


def test_formatter_includes_request_id_when_set():
    data = json.loads(JsonFormatter().format(_record(request_id="abc123")))
    assert data["request_id"] == "abc123"
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `python -m pytest tests/test_logging.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'logging_setup'`

- [ ] **Step 3: Реализовать `logging_setup.py`**

```python
"""JSON-логирование для PetBank.

Каждое лог-событие пишется одной JSON-строкой в stdout. В каждую запись через
record factory добавляется request_id текущего HTTP-запроса — так логи одного
запроса (включая бизнес-логи в make_decision) связываются между собой.
"""

import contextvars
import datetime
import json
import logging
import sys

# request_id текущего запроса; вне запроса — пустая строка.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

# Имя нашего хендлера — чтобы setup_logging был идемпотентным и не трогал чужие
# хендлеры (например, тот, что добавляет pytest caplog).
_HANDLER_NAME = "petbank_json"


class JsonFormatter(logging.Formatter):
    """Сериализует LogRecord в одну JSON-строку."""

    # Стандартные атрибуты LogRecord текущей версии Python — всё, что НЕ в этом
    # наборе, считается structured-полем из extra и попадает в JSON.
    _RESERVED = set(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    ) | {"message", "asctime", "request_id"}

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(
            record.created, datetime.timezone.utc
        )
        payload = {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "")
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in self._RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


# Базовая фабрика записей — захватываем один раз, чтобы повторные setup_logging
# не оборачивали фабрику многократно.
_BASE_FACTORY = logging.getLogRecordFactory()


def _install_request_id_factory() -> None:
    def factory(*args, **kwargs):
        record = _BASE_FACTORY(*args, **kwargs)
        record.request_id = request_id_ctx.get()
        return record

    logging.setLogRecordFactory(factory)


def setup_logging(level: int = logging.INFO) -> None:
    """Идемпотентно: один JSON-хендлер на stdout + инъекция request_id."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == _HANDLER_NAME:
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.name = _HANDLER_NAME
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    _install_request_id_factory()
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `python -m pytest tests/test_logging.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Коммит**

```bash
git add logging_setup.py tests/test_logging.py
git commit -m "feat: JSON-форматтер логов и setup_logging с инъекцией request_id"
```

---

### Task 2: Подключить `setup_logging` в `server.py` и обогатить лог решения

**Files:**
- Modify: `server.py` (блок настройки логирования у начала файла; функция `make_decision`; функция `run`)
- Test: `tests/test_logging.py`

**Interfaces:**
- Consumes: `setup_logging`, `request_id_ctx` из Task 1
- Produces: лог итогового решения с `extra={"application_id": ..., "status": ...}`

- [ ] **Step 1: Написать падающий тест на structured-поля лога решения**

Добавить в `tests/test_logging.py`:

```python
from datetime import date

import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture()
def client():
    return TestClient(app)


def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "country": "Россия",
        "birth_date": born.isoformat(),
    }


def test_decision_log_has_structured_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="server"):
        client.post("/applications", json=_adult_payload())
    finals = [r for r in caplog.records if getattr(r, "status", None) == "approved"]
    assert finals, "ожидали итоговый лог решения со status=approved"
    assert all(getattr(r, "application_id", None) for r in finals)
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `python -m pytest tests/test_logging.py::test_decision_log_has_structured_fields -v`
Expected: FAIL (у записи нет атрибута `status`)

- [ ] **Step 3: Правки в `server.py`**

3a. Заменить блок настройки логирования у начала файла. Было:

```python
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

Стало:

```python
from logging_setup import request_id_ctx, setup_logging

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("petbank.access")

# Настраиваем JSON-логирование при импорте — чтобы оно работало и под тестами,
# которые импортируют app напрямую, не вызывая run().
setup_logging()
```

3b. Добавить недостающие импорты к началу файла (рядом с существующими `import json` — если его нет, добавить; `import time`; в строке `from fastapi import FastAPI` добавить `Request`):

```python
import json
import time
```
```python
from fastapi import FastAPI, Request
```

3c. В `make_decision` обогатить итоговый лог решения. Было:

```python
    status = "approved" if not reasons else "declined"
    logger.info("Заявка %s — итог: %s", application_id, status.upper())
```

Стало:

```python
    status = "approved" if not reasons else "declined"
    logger.info(
        "Заявка %s — итог: %s", application_id, status.upper(),
        extra={"application_id": application_id, "status": status},
    )
```

3d. В `run()` переключить uvicorn на нашу конфигурацию логов. Было:

```python
    uvicorn.run(app, host=host, port=port)
```

Стало:

```python
    setup_logging()
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
```

- [ ] **Step 4: Запустить новый тест и весь набор**

Run: `python -m pytest tests/test_logging.py::test_decision_log_has_structured_fields -v`
Expected: PASS

Run: `python -m pytest -q`
Expected: все существующие тесты по-прежнему зелёные

- [ ] **Step 5: Коммит**

```bash
git add server.py tests/test_logging.py
git commit -m "feat: JSON-логирование в server.py + structured-поля лога решения"
```

---

### Task 3: Middleware логирования HTTP-запросов

**Files:**
- Modify: `server.py` (добавить middleware и хелпер тела рядом с определением `app`)
- Test: `tests/test_logging.py`

**Interfaces:**
- Consumes: `request_id_ctx`, `access_logger`, `setup_logging` (уже подключены в Task 2)
- Produces: на каждый запрос — лог-запись с `event="http_request"`; заголовок ответа `X-Request-ID`

- [ ] **Step 1: Написать падающие тесты middleware**

Добавить в `tests/test_logging.py`:

```python
def _access_records(caplog):
    return [r for r in caplog.records if getattr(r, "event", None) == "http_request"]


def test_request_metadata_logged_and_header_set(client, caplog):
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        resp = client.post("/applications", json=_adult_payload())
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")
    records = _access_records(caplog)
    assert len(records) == 1
    rec = records[0]
    assert rec.method == "POST"
    assert rec.path == "/applications"
    assert rec.status_code == 200
    assert isinstance(rec.duration_ms, float)


def test_full_request_body_logged(client, caplog):
    payload = _adult_payload()
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        client.post("/applications", json=payload)
    rec = _access_records(caplog)[0]
    assert rec.body["phone"] == payload["phone"]
    assert rec.body["last_name"] == payload["last_name"]


def test_invalid_json_body_logged_as_text(client, caplog):
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        resp = client.post(
            "/applications",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 422
    rec = _access_records(caplog)[0]
    assert rec.body == "{not json"


def test_incoming_request_id_is_reused(client, caplog):
    with caplog.at_level(logging.INFO, logger="petbank.access"):
        resp = client.get("/health", headers={"X-Request-ID": "test-123"})
    assert resp.headers["X-Request-ID"] == "test-123"
    assert _access_records(caplog)[0].request_id == "test-123"


def test_request_id_correlates_business_and_access_logs(client, caplog):
    with caplog.at_level(logging.INFO):
        client.post("/applications", json=_adult_payload())
    access = _access_records(caplog)[0]
    business = [r for r in caplog.records if r.name == "server"]
    assert access.request_id
    assert business
    assert all(getattr(r, "request_id", "") == access.request_id for r in business)
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `python -m pytest tests/test_logging.py -k "metadata or body or incoming or correlat" -v`
Expected: FAIL (нет middleware → нет записей `http_request`, нет заголовка)

- [ ] **Step 3: Реализовать middleware в `server.py`**

Сразу после `app = FastAPI(title="PetBank")` добавить:

```python
# Максимум байт тела запроса, попадающих в лог (защита от распухания логов).
MAX_BODY_BYTES = 10240


def _body_for_log(body_bytes: bytes) -> dict:
    """Готовит поле body для лога: JSON, иначе сырой текст; пусто → {}."""
    if not body_bytes:
        return {}
    chunk = body_bytes[:MAX_BODY_BYTES]
    try:
        return {"body": json.loads(chunk)}
    except (ValueError, UnicodeDecodeError):
        return {"body": chunk.decode("utf-8", errors="replace")}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_ctx.set(request_id)
    try:
        body_bytes = await request.body()
        start = time.perf_counter()
        status_code = 500
        exc: Exception | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as error:  # noqa: BLE001 — логируем и пробрасываем
            exc = error
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        access_logger.info(
            "%s %s %s", request.method, request.url.path, status_code,
            extra={
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
                **_body_for_log(body_bytes),
            },
            exc_info=exc,
        )
        if exc is not None:
            raise exc
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_ctx.reset(token)
```

> **Риск-нота (проверяется на Step 4):** чтение `await request.body()` до `call_next` не должно ломать парсинг тела ниже по стеку. В Starlette из состава FastAPI>=0.110 тело кэшируется и переигрывается в downstream — существующие тесты это подтвердят. Если какой-то тест из `test_http.py` (особенно `test_application_*`) внезапно упадёт с пустым телом/зависанием — заменить чтение на переигрывание тела через обёртку `receive` (закэшировать `body_bytes` и передать `call_next` новый `receive`, возвращающий `{"type": "http.request", "body": body_bytes}`).

- [ ] **Step 4: Запустить новые тесты и весь набор**

Run: `python -m pytest tests/test_logging.py -v`
Expected: PASS (все тесты файла)

Run: `python -m pytest -q`
Expected: весь набор зелёный (включая `test_http.py`, `test_decision.py`)

- [ ] **Step 5: Коммит**

```bash
git add server.py tests/test_logging.py
git commit -m "feat: middleware логирования HTTP-запросов (тело, request_id, X-Request-ID)"
```

---

## Self-Review (выполнено при написании плана)

- **Покрытие спецификации:** формат JSON и базовые поля → Task 1; инъекция `request_id` через record factory → Task 1; `setup_logging` идемпотентный + uvicorn `log_config=None`/`access_log=False` → Task 1/2; middleware (метаданные, тело, лимит, фоллбэк, `X-Request-ID`) → Task 3; корреляция бизнес-логов → Task 2/3; structured-поля решения → Task 2. ПДн в открытом виде — тест `test_full_request_body_logged`.
- **Плейсхолдеры:** не найдено; весь код приведён целиком.
- **Согласованность типов:** `request_id_ctx`, `JsonFormatter`, `setup_logging`, `access_logger`, `_body_for_log` используются под одинаковыми именами во всех задачах.
