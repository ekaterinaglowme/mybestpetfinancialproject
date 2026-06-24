# Защита /applications под нагрузкой + SLO — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Защитить `POST /applications` глобальным rate limiter (~100 RPS) и таймаутом-предохранителем, добавить метрики 429/таймаутов и SLO-наблюдаемость (p95≤200мс @ 100 RPS) в Grafana.

**Architecture:** Два рукописных HTTP-middleware (в стиле существующего `log_requests`): token-bucket лимитер и `asyncio.wait_for`-таймаут, оба только на `POST /applications`. Логика лимитера — в `ratelimit.py` (юнит-тестируется с инжектированными часами), таймаут — в `request_timeout.py`. Новые счётчики — в `metrics.py`. SLO — панели/алерт в Grafana через API.

**Tech Stack:** Python (stdlib `time`/`asyncio`), FastAPI/Starlette, `prometheus_client`. Новых зависимостей нет.

## Global Constraints

- **Без новых зависимостей.** Только stdlib + уже подключённые FastAPI/Starlette/`prometheus_client`.
- **Python floor 3.10** → использовать `asyncio.wait_for(...)`, **не** `asyncio.timeout` (3.11+).
- **Область защиты — только `POST /applications`.** Никогда не трогать `/health`, `/metrics`, `/`, `/docs`.
- **Дефолты (env):** `RATE_LIMIT_RPS=100`, `RATE_LIMIT_BURST` = значение `RATE_LIMIT_RPS`, `REQUEST_TIMEOUT_SECONDS=1.0`. Значение `0` отключает соответствующий механизм.
- **Ответы:** лимит → `429`, тело `{"detail": "rate limit exceeded"}`, заголовок `Retry-After`; таймаут → `503`, тело `{"detail": "request timeout"}`.
- **Порядок middleware:** в `server.py` вызвать `install_request_timeout(...)`, затем `install_rate_limiter(...)` — оба **до** строки `Instrumentator().instrument(app).expose(app)`. `log_requests` остаётся ниже (внешним). Итог снаружи внутрь: `log_requests` → instrumentator → rate-limit → timeout → хендлер.
- **Коммиты:** префиксы `feat:`/`docs:`; в конце сообщения — `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: TokenBucket (ядро лимитера)

**Files:**
- Create: `ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Produces: `class TokenBucket(rps: float, capacity: float, now: Callable[[], float] = time.monotonic)` с методами `allow() -> bool` и `seconds_until_token() -> float`.

- [ ] **Step 1: Написать падающие юнит-тесты**

Создать `tests/test_ratelimit.py`:
```python
from ratelimit import TokenBucket


def test_bucket_allows_up_to_capacity():
    clock = [0.0]
    b = TokenBucket(rps=100, capacity=3, now=lambda: clock[0])
    assert b.allow() is True
    assert b.allow() is True
    assert b.allow() is True
    assert b.allow() is False  # ёмкость исчерпана, время не шло


def test_bucket_refills_over_time():
    clock = [0.0]
    b = TokenBucket(rps=10, capacity=1, now=lambda: clock[0])
    assert b.allow() is True
    assert b.allow() is False
    clock[0] = 0.1  # 0.1с * 10 rps = 1 токен
    assert b.allow() is True


def test_seconds_until_token():
    clock = [0.0]
    b = TokenBucket(rps=2, capacity=1, now=lambda: clock[0])
    assert b.allow() is True
    assert b.seconds_until_token() == 0.5  # 1 токен / 2 rps


def test_seconds_until_token_zero_when_available():
    b = TokenBucket(rps=100, capacity=5, now=lambda: 0.0)
    assert b.seconds_until_token() == 0.0
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `pytest tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ratelimit'`.

- [ ] **Step 3: Реализовать `TokenBucket`**

Создать `ratelimit.py`:
```python
"""Глобальный rate limiter для PetBank (token bucket, in-memory)."""

import time
from collections.abc import Callable


class TokenBucket:
    """Token bucket: ёмкость `capacity`, пополнение `rps` токенов/с.

    Один async-процесс → проверка/списание синхронны и без локов.
    `now` инжектируется для детерминированных тестов.
    """

    def __init__(self, rps: float, capacity: float,
                 now: Callable[[], float] = time.monotonic):
        self.rps = rps
        self.capacity = capacity
        self._now = now
        self.tokens = float(capacity)
        self.updated = now()

    def _refill(self) -> None:
        t = self._now()
        self.tokens = min(self.capacity, self.tokens + (t - self.updated) * self.rps)
        self.updated = t

    def allow(self) -> bool:
        """Списывает 1 токен, если есть. True — пропустить, False — отказать."""
        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    def seconds_until_token(self) -> float:
        """Секунды до появления хотя бы 1 токена (для Retry-After)."""
        self._refill()
        if self.tokens >= 1 or self.rps <= 0:
            return 0.0
        return (1 - self.tokens) / self.rps
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `pytest tests/test_ratelimit.py -v`
Expected: PASS (4 теста).

- [ ] **Step 5: Commit**

```bash
git add ratelimit.py tests/test_ratelimit.py
git commit -m "feat: token bucket для rate limiter

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Rate-limit middleware + метрика + подключение

**Files:**
- Modify: `ratelimit.py` (добавить `install_rate_limiter`)
- Modify: `metrics.py` (добавить `RATE_LIMITED`)
- Modify: `server.py:21` (импорт), `server.py:26` (импорт метрик), `server.py:189-193` (конфиг + подключение)
- Test: `tests/test_ratelimit.py` (дополнить)

**Interfaces:**
- Consumes: `TokenBucket` (Task 1).
- Produces: `install_rate_limiter(app, *, bucket: TokenBucket, counter, path="/applications", method="POST") -> None` — регистрирует на `app` middleware, отдающий `429` при исчерпании bucket. `counter` — объект с методом `.inc()`.

- [ ] **Step 1: Написать падающий интеграционный тест middleware**

Дополнить `tests/test_ratelimit.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ratelimit import TokenBucket, install_rate_limiter


class FakeCounter:
    def __init__(self):
        self.n = 0

    def inc(self, amount: float = 1) -> None:
        self.n += 1


def _mini_app(bucket, counter):
    app = FastAPI()
    install_rate_limiter(app, bucket=bucket, counter=counter)

    @app.post("/applications")
    def apply():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    return TestClient(app)


def test_429_after_capacity_exhausted():
    counter = FakeCounter()
    # rps=0 → не пополняется: после 1 запроса bucket пуст
    client = _mini_app(TokenBucket(rps=0, capacity=1, now=lambda: 0.0), counter)
    assert client.post("/applications").status_code == 200
    resp = client.post("/applications")
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate limit exceeded"}
    assert "retry-after" in {k.lower() for k in resp.headers}
    assert counter.n == 1


def test_health_not_limited():
    counter = FakeCounter()
    client = _mini_app(TokenBucket(rps=0, capacity=1, now=lambda: 0.0), counter)
    for _ in range(5):
        assert client.get("/health").status_code == 200
    assert counter.n == 0
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/test_ratelimit.py::test_429_after_capacity_exhausted -v`
Expected: FAIL — `ImportError: cannot import name 'install_rate_limiter'`.

- [ ] **Step 3: Реализовать `install_rate_limiter`**

Дописать в `ratelimit.py` (импорты сверху файла):
```python
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
```
и в конец файла:
```python
def install_rate_limiter(app: FastAPI, *, bucket: TokenBucket, counter,
                         path: str = "/applications", method: str = "POST") -> None:
    """Вешает на `app` middleware: лимитирует method+path, иначе 429."""

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.method == method and request.url.path == path:
            if not bucket.allow():
                counter.inc()
                retry = max(1, round(bucket.seconds_until_token()))
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)
```

- [ ] **Step 4: Запустить новые тесты — проходят**

Run: `pytest tests/test_ratelimit.py -v`
Expected: PASS (6 тестов).

- [ ] **Step 5: Добавить метрику `RATE_LIMITED`**

В `metrics.py` дописать (рядом с другими, после существующих определений):
```python
RATE_LIMITED = Counter(
    "petbank_rate_limited_total",
    "Запросы к /applications, отклонённые rate limiter (429)",
)
```
(`Counter` уже импортирован в `metrics.py`.)

- [ ] **Step 6: Подключить лимитер в `server.py`**

`server.py:21` — расширить импорт:
```python
from fastapi import FastAPI, Request
```
(уже такой — без изменений; строка приведена для контекста).

`server.py:26` — расширить импорт метрик:
```python
from metrics import APPLICATION_AMOUNT_RUB, DECISIONS, RATE_LIMITED, REJECTION_REASONS
```

Добавить импорт лимитера после строки 26:
```python
from ratelimit import TokenBucket, install_rate_limiter
```

Заменить блок `server.py:189-193` (от `app = FastAPI(...)` до строки instrumentator) на:
```python
app = FastAPI(title="PetBank")

# --- Защита /applications под нагрузкой (env-конфиг; 0 = выключить) ---
_RATE_LIMIT_RPS = float(os.environ.get("RATE_LIMIT_RPS", "100"))
_RATE_LIMIT_BURST = float(os.environ.get("RATE_LIMIT_BURST") or _RATE_LIMIT_RPS)

if _RATE_LIMIT_RPS > 0:
    install_rate_limiter(
        app,
        bucket=TokenBucket(_RATE_LIMIT_RPS, _RATE_LIMIT_BURST),
        counter=RATE_LIMITED,
    )

# Prometheus-метрики: instrumentator сам поднимает GET /metrics и считает
# HTTP-метрики (rate / errors / latency по ручкам).
Instrumentator().instrument(app).expose(app)
```
(Лимитер регистрируется **до** instrumentator → 429 считается в `http_requests_total` и логируется `log_requests`.)

- [ ] **Step 7: Регрессионный тест — happy path жив**

Дополнить `tests/test_ratelimit.py`:
```python
from server import app as real_app


def test_real_app_applications_ok_with_request_id():
    client = TestClient(real_app)
    body = {
        "last_name": "Иванов", "first_name": "Иван", "phone": "+79991234567",
        "birth_date": "2000-05-15", "country": "Россия", "amount": 100000,
    }
    resp = client.post("/applications", json=body)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("approved", "declined")
    assert "X-Request-ID" in resp.headers  # log_requests остался внешним
```

- [ ] **Step 8: Запустить весь набор — зелёный**

Run: `pytest -q`
Expected: PASS (включая существующие тесты).

- [ ] **Step 9: Commit**

```bash
git add ratelimit.py metrics.py server.py tests/test_ratelimit.py
git commit -m "feat: rate limiter 100 RPS на /applications (429 + метрика)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Timeout middleware + метрика + подключение

**Files:**
- Create: `request_timeout.py`
- Modify: `metrics.py` (добавить `REQUEST_TIMEOUTS`)
- Modify: `server.py:26` (импорт метрики), новый импорт, блок подключения
- Test: `tests/test_timeout.py`

**Interfaces:**
- Produces: `install_request_timeout(app, *, seconds: float, counter, path="/applications", method="POST") -> None` — регистрирует middleware, отдающий `503` при превышении `seconds`.

- [ ] **Step 1: Написать падающий тест таймаута**

Создать `tests/test_timeout.py`:
```python
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from request_timeout import install_request_timeout


class FakeCounter:
    def __init__(self):
        self.n = 0

    def inc(self, amount: float = 1) -> None:
        self.n += 1


def _mini_app(seconds, counter):
    app = FastAPI()
    install_request_timeout(app, seconds=seconds, counter=counter)

    @app.post("/applications")
    async def slow():
        await asyncio.sleep(0.5)
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    return TestClient(app)


def test_timeout_returns_503():
    counter = FakeCounter()
    client = _mini_app(0.05, counter)
    resp = client.post("/applications")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "request timeout"}
    assert counter.n == 1


def test_fast_request_passes():
    counter = FakeCounter()
    client = _mini_app(0.05, counter)
    assert client.get("/health").status_code == 200
    assert counter.n == 0
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_timeout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'request_timeout'`.

- [ ] **Step 3: Реализовать `install_request_timeout`**

Создать `request_timeout.py`:
```python
"""Таймаут-предохранитель для PetBank.

Использует asyncio.wait_for (Python 3.10+). Прерывает только на await-точках;
синхронный make_decision быстрый — это формальный предохранитель от будущих
зависаний на I/O, а не жёсткий killer текущего хендлера.
"""

import asyncio

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse


def install_request_timeout(app: FastAPI, *, seconds: float, counter,
                            path: str = "/applications", method: str = "POST") -> None:
    """Вешает на `app` middleware: ограничивает время method+path, иначе 503."""

    @app.middleware("http")
    async def _timeout(request: Request, call_next):
        if request.method == method and request.url.path == path:
            try:
                return await asyncio.wait_for(call_next(request), timeout=seconds)
            except asyncio.TimeoutError:
                counter.inc()
                return JSONResponse({"detail": "request timeout"}, status_code=503)
        return await call_next(request)
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_timeout.py -v`
Expected: PASS (2 теста).

- [ ] **Step 5: Добавить метрику `REQUEST_TIMEOUTS`**

В `metrics.py` дописать:
```python
REQUEST_TIMEOUTS = Counter(
    "petbank_request_timeouts_total",
    "Запросы к /applications, прерванные по таймауту (503)",
)
```

- [ ] **Step 6: Подключить таймаут в `server.py`**

`server.py:26` — добавить `REQUEST_TIMEOUTS` в импорт метрик:
```python
from metrics import (APPLICATION_AMOUNT_RUB, DECISIONS, RATE_LIMITED,
                     REJECTION_REASONS, REQUEST_TIMEOUTS)
```

Добавить импорт после импорта лимитера:
```python
from request_timeout import install_request_timeout
```

В блоке подключения (Task 2, Step 6) добавить вызов таймаута **перед** лимитером (таймаут — внутренний). Итоговый блок:
```python
app = FastAPI(title="PetBank")

# --- Защита /applications под нагрузкой (env-конфиг; 0 = выключить) ---
_RATE_LIMIT_RPS = float(os.environ.get("RATE_LIMIT_RPS", "100"))
_RATE_LIMIT_BURST = float(os.environ.get("RATE_LIMIT_BURST") or _RATE_LIMIT_RPS)
_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "1.0"))

if _REQUEST_TIMEOUT_SECONDS > 0:
    install_request_timeout(
        app, seconds=_REQUEST_TIMEOUT_SECONDS, counter=REQUEST_TIMEOUTS,
    )
if _RATE_LIMIT_RPS > 0:
    install_rate_limiter(
        app,
        bucket=TokenBucket(_RATE_LIMIT_RPS, _RATE_LIMIT_BURST),
        counter=RATE_LIMITED,
    )

# Prometheus-метрики: instrumentator сам поднимает GET /metrics и считает
# HTTP-метрики (rate / errors / latency по ручкам).
Instrumentator().instrument(app).expose(app)
```
(Порядок: таймаут (внутр.) → лимитер → instrumentator → log_requests (внеш.).)

- [ ] **Step 7: Запустить весь набор — зелёный**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add request_timeout.py metrics.py server.py tests/test_timeout.py
git commit -m "feat: таймаут-предохранитель на /applications (503 + метрика)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Метрики на /metrics + документация

**Files:**
- Test: `tests/test_metrics.py` (дополнить)
- Modify: `README.md`

**Interfaces:**
- Consumes: `RATE_LIMITED`, `REQUEST_TIMEOUTS` (Tasks 2, 3) — экспонируются на `/metrics` при импорте `server`/`metrics`.

- [ ] **Step 1: Написать падающий тест экспонирования счётчиков**

Дополнить `tests/test_metrics.py` (использует существующий `TestClient`/`app` паттерн файла):
```python
def test_protection_counters_exposed():
    from fastapi.testclient import TestClient
    from server import app

    client = TestClient(app)
    body = client.get("/metrics").text
    assert "petbank_rate_limited_total" in body
    assert "petbank_request_timeouts_total" in body
```

- [ ] **Step 2: Запустить — падает (или пройдёт, если код Task 2–3 уже на месте)**

Run: `pytest tests/test_metrics.py::test_protection_counters_exposed -v`
Expected: PASS (счётчики уже зарегистрированы в `metrics.py` и видны на `/metrics` даже при нулевом значении). Если FAIL — счётчики не зарегистрированы/не импортированы; вернуться к Task 2 Step 5 / Task 3 Step 5.

> Примечание: инкремент 429/таймаута проверяется юнит-тестами middleware (`FakeCounter`) в Tasks 2–3; здесь проверяем именно факт экспонирования реальных счётчиков на `/metrics`.

- [ ] **Step 3: Документировать в `README.md`**

Дописать в `README.md` новый раздел (в конец файла):
```markdown
## Защита под нагрузкой и SLO

`POST /applications` защищён от перегрузки (только эта ручка; `/health`,
`/metrics`, `/`, `/docs` не лимитируются):

- **Rate limiter** — глобальный token bucket. Сверх лимита → `429` +
  `Retry-After`. Метрика `petbank_rate_limited_total`.
- **Таймаут-предохранитель** — долгий запрос → `503`. Метрика
  `petbank_request_timeouts_total`. Прерывает только на `await`-точках
  (формальный предохранитель: `make_decision` синхронный и быстрый).

Конфигурация (env, `0` = выключить):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `RATE_LIMIT_RPS` | `100` | Пополнение токенов (RPS) |
| `RATE_LIMIT_BURST` | `= RATE_LIMIT_RPS` | Ёмкость bucket (всплеск) |
| `REQUEST_TIMEOUT_SECONDS` | `1.0` | Таймаут запроса |

**SLO:** при нагрузке до 100 RPS p95 латентности `/applications` ≤ 200 мс.
Наблюдается в Grafana (дашборд `petbank-business`, секция «SLO»).
```

> Примечание: подробные доки `docs/ARCHITECTURE.md`/`docs/OPERATIONS.md` сейчас живут вне `main` (untracked / ветка `docs/project-documentation`). Их env-таблицу и раздел SLO обновить там отдельно — вне git-объёма этой ветки.

- [ ] **Step 4: Запустить весь набор — зелёный**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_metrics.py README.md
git commit -m "docs: README про защиту под нагрузкой + тест экспонирования счётчиков

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: SLO-наблюдаемость в Grafana (live, вне git)

> Выполняется через Grafana API (инстанс `212.147.238.3:3000`, орг 3, роль Editor), **не коммитится в репозиторий**. Дашборд `petbank-business` уже существует.

**Interfaces:**
- Consumes: метрики `http_request_duration_highr_seconds_bucket`, `http_requests_total`, `petbank_rate_limited_total`, `petbank_request_timeouts_total` (последние две наполнятся только после деплоя кода Tasks 2–3).

- [ ] **Step 1: Добавить в дашборд `petbank-business` секцию «SLO»**

Через `POST /api/dashboards/db` (overwrite) добавить ряд «SLO» с панелями (datasource `prom-3`):
- **p95 vs SLO** (timeseries, unit `s`, пороговая линия 0.2):
  `histogram_quantile(0.95, sum(rate(http_request_duration_highr_seconds_bucket{job="petbank"}[$__rate_interval])) by (le))`
- **RPS /applications** (timeseries, ориентир 100):
  `sum(rate(http_requests_total{job="petbank",handler="/applications"}[$__rate_interval]))`
- **SLO compliance — доля не-5xx** (stat, unit `percentunit`):
  `sum(rate(http_requests_total{job="petbank",handler="/applications",status!~"5xx"}[$__rate_interval])) / sum(rate(http_requests_total{job="petbank",handler="/applications"}[$__rate_interval]))`
- **429/сек** (timeseries): `sum(rate(petbank_rate_limited_total{job="petbank"}[$__rate_interval]))`
- **Таймауты/сек** (timeseries): `sum(rate(petbank_request_timeouts_total{job="petbank"}[$__rate_interval]))`

- [ ] **Step 2: Проверить, что панели рисуются (запросы возвращают данные)**

Через прокси `GET /api/datasources/proxy/uid/prom-3/api/v1/query` прогнать exprs p95/RPS/compliance — должны вернуть значения. (429/таймауты будут пустыми до деплоя нового кода — это ожидаемо, зафиксировать.)

- [ ] **Step 3: Создать алерт-правило Grafana «p95 > 200 мс в течение 5 минут»**

Через Grafana API создать alert rule на expr p95 > 0.2 за 5м. Best-effort как Editor; если contact point требует Admin — оставить правило без внешней нотификации и зафиксировать это пользователю.

- [ ] **Step 4: Зафиксировать результат пользователю**

Сообщить: ссылки на обновлённый дашборд, какие панели наполнены сейчас, какие — после деплоя; статус алерта (создан/ограничен правами).

---

## Доставка

1. **PR в репозиторий** — Tasks 1–4 (ветка `feat/ratelimit-timeout-slo` от `main`).
2. **Grafana** — Task 5, на живом инстансе через API (вне git).

После Tasks 1–4 и зелёного `pytest -q` — финальная верификация: запустить приложение, `curl` на `/applications` (200 + `X-Request-ID`) и `curl /metrics | grep -E 'petbank_rate_limited_total|petbank_request_timeouts_total'`.
