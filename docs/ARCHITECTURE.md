# 🏛️ Архитектура PetBank

Документ описывает, **как устроено приложение, из каких частей состоит, как они
связаны и что происходит при запросе**. Описывается актуальная ветка `main`.

Содержание:

1. [Обзор с высоты птичьего полёта](#1-обзор-с-высоты-птичьего-полёта)
2. [Модули и их ответственность](#2-модули-и-их-ответственность)
3. [Слои приложения](#3-слои-приложения)
4. [Модели данных (Pydantic)](#4-модели-данных-pydantic)
5. [Жизненный цикл запроса (end-to-end)](#5-жизненный-цикл-запроса-end-to-end)
6. [Граф зависимостей модулей](#6-граф-зависимостей-модулей)
7. [Observability: логи и метрики](#7-observability-логи-и-метрики)
8. [Конфигурация (переменные окружения)](#8-конфигурация-переменные-окружения)
9. [Принятые архитектурные решения](#9-принятые-архитектурные-решения)

---

## 1. Обзор с высоты птичьего полёта

PetBank — это **одно ASGI-приложение** на FastAPI. Никакой базы данных, очередей
или внешних сервисов на проде нет: заявка приходит по HTTP, синхронно проверяется
по бизнес-правилам и тут же возвращается решение. Состояние нигде не хранится —
каждый запрос самодостаточен (stateless).

```
                              ┌───────────────────────────────────────────┐
   HTTP-клиент                │            КОНТЕЙНЕР petbank                │
  (Postman / curl /           │                                            │
   фронт / Prometheus)        │   ┌─────────┐     ┌────────────────────┐   │
        │                     │   │ Uvicorn │ ──► │  FastAPI (app)      │   │
        │   POST /applications│   │ (ASGI-  │     │  ┌──────────────┐   │   │
        ├────────────────────►│   │  сервер)│     │  │ middleware   │   │   │
        │                     │   └─────────┘     │  │ логирования  │   │   │
        │   GET /health       │                   │  ├──────────────┤   │   │
        │   GET /metrics      │                   │  │ instrumentator│  │   │
        │   GET /docs         │                   │  │ (метрики)    │   │   │
        │                     │                   │  ├──────────────┤   │   │
        │   200 / 422         │                   │  │ роуты +      │   │   │
        │◄────────────────────│                   │  │ Pydantic     │   │   │
        │                     │                   │  │ валидация    │   │   │
        │                     │                   │  ├──────────────┤   │   │
        │                     │                   │  │ make_decision│   │   │
        │                     │                   │  └──────────────┘   │   │
        │                     │                   └────────────────────┘   │
        │                     │         │ stdout (JSON-логи)               │
        │                     └─────────┼──────────────────────────────────┘
        │                               ▼
        │                         docker logs petbank
```

Внутри приложения запрос проходит через **конвейер**: middleware логирования →
middleware метрик (instrumentator) → роутинг → Pydantic-валидация → бизнес-логика
→ сериализация ответа. Подробно — в разделе [5](#5-жизненный-цикл-запроса-end-to-end).

---

## 2. Модули и их ответственность

Проект намеренно маленький: **4 Python-модуля**, каждый с одной зоной
ответственности.

### `server.py` — сердце приложения

Самый большой модуль. Содержит всё, что касается HTTP и бизнес-логики:

| Часть | Что это |
|---|---|
| **Pydantic-модели** | `ApplicationRequest` (вход), `ApplicantInfo`, `ApplicationDecision` (выход) |
| **Бизнес-логика** | `calculate_age(birth_date, today)` — полных лет; `make_decision(payload)` — применяет правила, считает метрики, пишет логи, возвращает решение |
| **Константы правил** | `MIN_AGE = 18`, `MAX_AGE = 35`, `BLOCKED_COUNTRIES = {"китай"}` |
| **FastAPI-приложение** | `app = FastAPI(title="PetBank")` |
| **Middleware** | `log_requests` — логирует каждый HTTP-запрос (см. [раздел 7](#7-observability-логи-и-метрики)) |
| **Подключение метрик** | `Instrumentator().instrument(app).expose(app)` — поднимает `/metrics` |
| **Роуты** | `GET /health`, `GET /`, `POST /applications` |
| **Запуск** | `run(host, port)` — настраивает логи и стартует Uvicorn |

Ключевая идея: **бизнес-логика (`calculate_age`, `make_decision`) — это чистые
функции, не зависящие от FastAPI**. Их можно тестировать без HTTP (см.
`tests/test_decision.py`). FastAPI — лишь тонкий транспортный слой сверху.

### `logging_setup.py` — JSON-логирование

Отдельный модуль, чтобы не раздувать `server.py`. Отвечает за то, чтобы **каждое
лог-событие писалось одной JSON-строкой в stdout** и чтобы логи одного запроса
были связаны общим `request_id`.

| Символ | Назначение |
|---|---|
| `JsonFormatter` | `logging.Formatter`, сериализующий `LogRecord` → JSON-строка |
| `request_id_ctx` | `contextvars.ContextVar` — хранит `request_id` текущего запроса |
| `setup_logging(level)` | Идемпотентно ставит один JSON-хендлер на root-логгер и фабрику записей |
| `_install_request_id_factory()` | Подменяет фабрику `LogRecord`, чтобы в **каждую** запись автоматически попадал `request_id` из контекста |

Почему через `contextvars` и record factory, а не через проброс аргумента: так
`request_id` попадает даже в логи, которые пишет не наш код (например, логи
самого Uvicorn) и в бизнес-логи `make_decision` — **без изменения их сигнатур**.

### `metrics.py` — бизнес-метрики Prometheus

Определяет кастомные метрики предметной области. Они регистрируются в глобальном
`REGISTRY` библиотеки `prometheus_client` и автоматически попадают в тот же
`/metrics`, который поднимает instrumentator.

| Метрика | Тип | Лейблы | Смысл |
|---|---|---|---|
| `petbank_decisions_total` | Counter | `status`, `country` | Сколько заявок обработано, с разбивкой по итогу и стране |
| `petbank_rejection_reasons_total` | Counter | `reason` | Причины отказов по **категориям** (не сырой текст) |
| `petbank_application_amount_rub` | Histogram | — | Распределение запрошенных сумм по корзинам |
| `petbank_app_info` | Info | `version`, `commit` | Версия и git-коммит образа (build-info) |

`reason` — ограниченный набор: `age_below_min`, `age_above_max`,
`blocked_country` (чтобы не плодить кардинальность лейблов сырым текстом).

### `main.py` — точка входа

Три строки. Существует ради удобства: на нём в PyCharm удобно жать зелёную
кнопку **Run**. Делает `from server import run; run()`. Из терминала эквивалентно
`python server.py`.

---

## 3. Слои приложения

Логически код делится на слои. Запрос «спускается» сверху вниз, ответ —
поднимается обратно.

```
┌─────────────────────────────────────────────────────────────────┐
│  ТРАНСПОРТ            Uvicorn (ASGI-сервер)                       │
│                      принимает TCP/HTTP, говорит с app по ASGI    │
├─────────────────────────────────────────────────────────────────┤
│  MIDDLEWARE          1) log_requests   — логи + request_id        │
│  (сквозные слои)     2) instrumentator — HTTP-метрики (RED)       │
├─────────────────────────────────────────────────────────────────┤
│  МАРШРУТИЗАЦИЯ       FastAPI router: путь+метод → функция-обработчик│
├─────────────────────────────────────────────────────────────────┤
│  ВАЛИДАЦИЯ           Pydantic: JSON тела → ApplicationRequest      │
│                      (типы, обязательность, кастомные проверки)    │
├─────────────────────────────────────────────────────────────────┤
│  БИЗНЕС-ЛОГИКА       make_decision() / calculate_age()            │
│  (чистые функции)    правила возраста и страны → reasons, status   │
│                      сюда же: инкременты метрик, бизнес-логи       │
├─────────────────────────────────────────────────────────────────┤
│  СЕРИАЛИЗАЦИЯ        Pydantic: dict → ApplicationDecision → JSON   │
│  ОТВЕТА             (response_model)                              │
└─────────────────────────────────────────────────────────────────┘
```

Граница «грязного» и «чистого» проходит между **валидацией** и **бизнес-логикой**:
выше — FastAPI/HTTP-специфика, ниже — чистый Python, который ничего не знает про
HTTP и легко тестируется.

---

## 4. Модели данных (Pydantic)

Вся валидация входа и форма выхода описаны Pydantic-моделями в `server.py`. Это
**единственный источник правды**: из них FastAPI сам генерирует OpenAPI-схему и
Swagger UI.

### `ApplicationRequest` — входящая заявка

```python
class ApplicationRequest(BaseModel):
    last_name:   str                 # Фамилия      — обязательна, непустая
    first_name:  str                 # Имя          — обязательна, непустая
    middle_name: str = ""            # Отчество     — опционально
    phone:       str                 # Телефон      — обязателен, непустой
    birth_date:  date                # Дата рождения — YYYY-MM-DD, не в будущем
    country:     str                 # Страна       — обязательна, непустая
    amount:      float | None = None # Сумма        — опционально, ≥ 0
```

Кастомные валидаторы (что именно проверяется — в [API.md](API.md#валидация-полей)):

| Поле(я) | Валидатор | Правило |
|---|---|---|
| `last_name`, `first_name`, `phone`, `country` | `strip_and_require_nonempty` (before) | строка; обрезать пробелы; пустая после обрезки → ошибка |
| `middle_name` | `strip_middle_name` (before) | `None` → `""`; не-строка → ошибка; иначе обрезать |
| `birth_date` | `birth_date_not_future` (after) | дата в будущем → ошибка |
| `amount` | `validate_amount` (before) | `None` ок; `bool`/не-число/отрицательное → ошибка; иначе `float` |

> Тонкость: `bool` в Python — подкласс `int`, поэтому `amount=True` пришлось бы
> принять как `1`. Валидатор явно отсекает `bool`.

### `ApplicationDecision` — ответ (решение)

```python
class ApplicantInfo(BaseModel):
    full_name: str   # "Фамилия Имя Отчество" (отчество — если есть)
    age: int         # полных лет на сегодня
    phone: str

class ApplicationDecision(BaseModel):
    application_id: str        # uuid4 — id этой заявки
    status: str                # "approved" | "declined"
    applicant: ApplicantInfo
    reasons: list[str]         # причины отказа (пусто, если approved)
    received_at: str           # ISO-время приёма, до секунд
```

Эндпоинт объявлен с `response_model=ApplicationDecision` — FastAPI валидирует и
сериализует выход по этой модели.

---

## 5. Жизненный цикл запроса (end-to-end)

Разберём самый интересный путь — `POST /applications`. Цифры на схеме
соответствуют шагам ниже.

```
  Клиент                Uvicorn   middleware    instrumentator   роут+Pydantic   make_decision
    │  POST /applications  │           │              │                │               │
    │ ────────────────────►│           │              │                │               │
    │                      │ ①ASGI     │              │                │               │
    │                      │ ─────────►│ ② request_id │                │               │
    │                      │           │   читать тело│                │               │
    │                      │           │   старт таймера                │              │
    │                      │           │ ────────────►│ ③ счёт HTTP-метрик            │
    │                      │           │              │ ──────────────►│ ④ валидация   │
    │                      │           │              │                │   JSON→модель │
    │                      │           │              │                │ ─────────────►│ ⑤ правила,
    │                      │           │              │                │               │   метрики,
    │                      │           │              │                │               │   логи
    │                      │           │              │                │ ◄─────────────│ dict
    │                      │           │              │ ◄──────────────│ ⑥ JSON 200    │
    │                      │           │ ◄────────────│   (по response_model)         │
    │                      │           │ ⑦ access-лог │                                │
    │                      │           │   X-Request-ID в ответ                        │
    │  200 + решение       │ ◄─────────│              │                                │
    │ ◄────────────────────│           │              │                                │
```

**Шаги по порядку:**

1. **Uvicorn** принимает HTTP-соединение и передаёт запрос FastAPI-приложению по
   протоколу ASGI.

2. **Middleware `log_requests`** (внешний слой):
   - берёт `request_id` из заголовка `X-Request-ID`, а если его нет — генерирует
     `uuid4().hex`;
   - кладёт его в `request_id_ctx` (ContextVar) — теперь **любой** лог в рамках
     этого запроса автоматически получит этот id;
   - читает тело запроса `await request.body()` (Starlette кэширует тело, так что
     обработчик ниже сможет прочитать его повторно);
   - запускает таймер `time.perf_counter()`.

3. **Instrumentator (middleware метрик)** оборачивает обработку и по завершении
   увеличивает стандартные HTTP-метрики (RED: Rate/Errors/Duration по ручке).

4. **Маршрутизация + Pydantic-валидация**: FastAPI находит обработчик
   `create_application(payload: ApplicationRequest)` и парсит тело в модель
   `ApplicationRequest`. Если данные невалидны — дальше код не идёт, сразу
   возвращается **422** со списком ошибок (`{"detail": [...]}`).

5. **`make_decision(payload)`** (чистая бизнес-логика):
   - `calculate_age()` считает полных лет;
   - пишет лог «Заявка {id}: …»;
   - проверяет три правила, на каждое нарушение добавляет причину в `reasons`,
     инкрементит `petbank_rejection_reasons_total{reason=...}` и пишет лог отказа;
   - `status = approved`, если `reasons` пуст, иначе `declined`;
   - инкрементит `petbank_decisions_total{status, country}`, при наличии суммы —
     `petbank_application_amount_rub.observe(amount)`;
   - пишет итоговый лог с `extra={application_id, status}`;
   - возвращает `dict` с решением.

6. **Сериализация ответа**: FastAPI приводит `dict` к `ApplicationDecision` и
   отдаёт JSON со статусом **200**.

7. **Возврат через middleware**: `log_requests` фиксирует длительность, пишет
   **одну** access-запись (`event="http_request"` с методом, путём, кодом,
   `duration_ms`, IP клиента и **полным телом запроса**), добавляет в ответ
   заголовок `X-Request-ID` и в `finally` очищает ContextVar.

> Прочие эндпоинты (`GET /health`, `GET /`, `GET /metrics`, `GET /docs`) проходят
> тот же конвейер middleware, но без шагов валидации тела и `make_decision`.

---

## 6. Граф зависимостей модулей

Кто кого импортирует:

```
        main.py
           │  from server import run
           ▼
        server.py ──────────────► logging_setup.py   (request_id_ctx, setup_logging)
           │                              ▲
           │ from metrics import ...       │ (фабрика логов кладёт request_id
           ▼                               │  из ContextVar в каждую запись)
        metrics.py                         │
           │                               │
           ▼                               │
   prometheus_client (REGISTRY) ◄── instrumentator поднимает /metrics из того же реестра
```

- `main.py` → `server.py` (только чтобы вызвать `run`).
- `server.py` → `logging_setup.py` (берёт `request_id_ctx`, зовёт `setup_logging`)
  и `metrics.py` (инкрементит счётчики в `make_decision`).
- `metrics.py` и `logging_setup.py` ни от чего внутри проекта не зависят — это
  «листья», их можно тестировать изолированно.
- Связь логов и метрик с HTTP-слоем — **косвенная**: метрики живут в глобальном
  реестре `prometheus_client`, логи — в глобальной конфигурации `logging`.

---

## 7. Observability: логи и метрики

Две независимые подсистемы наблюдаемости. Обе настраиваются при импорте `server`,
поэтому работают и под тестами, и на проде.

### Логи (структурные, JSON)

- **Формат:** одна JSON-строка на событие, в `stdout`. На проде собираются через
  `docker logs petbank`.
- **Поля записи** (формирует `JsonFormatter`):

  | Поле | Откуда |
  |---|---|
  | `timestamp` | время события, ISO-8601 в UTC (`…Z`) |
  | `level` | уровень (`INFO`, …) |
  | `logger` | имя логгера (`server`, `petbank.access`, `uvicorn…`) |
  | `message` | отрендеренный текст |
  | `request_id` | id запроса (опускается, если пустой) |
  | `exc_info` | трейсбэк, если было исключение |
  | *любые из* `extra={...}` | напр. `event`, `method`, `path`, `status_code`, `duration_ms`, `client_ip`, `body`, `application_id`, `status` |

- **Два вида событий на один запрос, связанные `request_id`:**
  - **бизнес-логи** из `make_decision` (logger `server`): приём заявки, причины
    отказа, итог;
  - **access-лог** из middleware (logger `petbank.access`): `event=http_request`
    с метаданными и полным телом запроса.

- **Как `request_id` попадает везде:** middleware кладёт его в `ContextVar`, а
  подменённая фабрика `LogRecord` читает его оттуда для каждой записи. Проброс
  через аргументы не нужен.

- **На проде** `run()` зовёт `uvicorn.run(..., log_config=None, access_log=False)`
  — чтобы Uvicorn не перетирал нашу конфигурацию логов и не дублировал
  access-логи (единственный их источник — наше middleware).

Пример (два события одного запроса, один `request_id`):

```json
{"timestamp":"2026-06-18T12:34:56.781Z","level":"INFO","logger":"server","message":"Заявка 3f2a… — итог: APPROVED","request_id":"7c9e…","application_id":"3f2a…","status":"approved"}
{"timestamp":"2026-06-18T12:34:56.789Z","level":"INFO","logger":"petbank.access","message":"POST /applications 200","request_id":"7c9e…","event":"http_request","method":"POST","path":"/applications","status_code":200,"duration_ms":12.4,"client_ip":"172.18.0.1","body":{"last_name":"Иванов",...}}
```

> ⚠️ **ПДн в логах.** Тело запроса (ФИО, телефон, дата рождения) пишется в лог
> **в открытом виде** — это осознанное решение для учебного стенда. Следствие:
> доступ к хранилищу логов должен быть ограничен, срок хранения — небольшим. См.
> [STATUS.md → техдолг](STATUS.md#известные-пробелы-и-техдолг).

### Метрики (Prometheus)

- **Эндпоинт:** `GET /metrics`, тот же порт 8000, **без авторизации** (так задумано
  — скрейпит внутренний Prometheus заказчика). Формат — текстовый Prometheus
  exposition (`Content-Type: text/plain`).
- **Два источника метрик в одном `/metrics`:**
  - **HTTP-метрики (RED)** от `prometheus-fastapi-instrumentator` — автоматически:
    `http_requests_total{…,status="2xx"}`, `http_request_duration_seconds_*` и т.п.
  - **Бизнес-метрики** из `metrics.py` (см. [раздел 2](#metricspy--бизнес-метрики-prometheus))
    — наполняются инкрементами в `make_decision`.
- **Build-info:** `petbank_app_info{version, commit}` берёт `commit` из env
  `GIT_COMMIT` (CI прокидывает `github.sha` через build-arg), `version` — из
  `APP_VERSION` или первых 12 символов коммита, иначе `dev`/`unknown`.

---

## 8. Конфигурация (переменные окружения)

Приложение конфигурируется минимально, через переменные окружения:

| Переменная | По умолчанию | Где используется | Назначение |
|---|---|---|---|
| `PORT` | `8000` | `server.run()` | Порт, который слушает Uvicorn |
| `GIT_COMMIT` | `unknown` | `metrics.py` | Коммит образа → метрика `petbank_app_info`. В CI = `github.sha` (build-arg → ENV в Dockerfile) |
| `APP_VERSION` | *(нет)* | `metrics.py` | Версия для `petbank_app_info`; если не задана — первые 12 символов `GIT_COMMIT` или `dev` |

Порт можно также передать аргументом командной строки: `python server.py 8080`
(приоритетнее, чем env `PORT`).

Бизнес-правила (`MIN_AGE`, `MAX_AGE`, `BLOCKED_COUNTRIES`) — **не** переменные
окружения, а константы в `server.py`. Менять их — правкой кода (см.
[CHECKLISTS.md](CHECKLISTS.md#➕-добавить-или-изменить-бизнес-правило)).

---

## 9. Принятые архитектурные решения

Почему сделано так, а не иначе (из спецификаций в `docs/superpowers/specs/`):

1. **FastAPI вместо голого `http.server`.** Изначально сервер был на stdlib.
   Перешли на FastAPI ради валидации, авто-документации (Swagger) и стандартных
   инструментов экосистемы. Бизнес-логику при этом оставили чистыми функциями.

2. **Pydantic как единственный источник схемы.** Раньше валидация была ручной
   (`validate_payload`), а схема — отдельно в `openapi.yaml`, и они расходились.
   Теперь модель `ApplicationRequest` — и валидация, и схема, и Swagger
   одновременно. `openapi.yaml` оставлен как legacy для импорта в Postman.
   - Побочный эффект: ошибки валидации теперь в формате FastAPI — **HTTP 422**
     `{"detail":[...]}`, а не прежние кастомные 400. Это сознательный breaking
     change, описанный в спеке.

3. **Логи и метрики — отдельные модули.** Чтобы не раздувать `server.py` и
   тестировать изолированно. Связь с запросом — через глобальные механизмы
   (`logging`-конфиг, `prometheus_client` REGISTRY, `ContextVar`), без явного
   проброса по сигнатурам.

4. **`request_id` через `contextvars`, а не аргумент.** Чтобы корреляция работала
   для логов любого происхождения (включая Uvicorn) без правки сигнатур функций.

5. **Stateless, без БД.** Решение по заявке не сохраняется — для учебного сервиса
   состояние не нужно. `docker-compose.yml` с Postgres — это незакоммиченный
   задел на будущее, приложением пока не используется.

6. **Деплой через `docker run`, а не systemd.** Автозапуск после ребута/падения
   обеспечивает сам Docker (`--restart unless-stopped`) — отдельный systemd-юнит
   не нужен. Подробнее — [OPERATIONS.md](OPERATIONS.md#деплой-на-vm).

---

Дальше: [как дёрнуть API](API.md) · [как запускать и деплоить](OPERATIONS.md) ·
[чек-листы задач](CHECKLISTS.md) · [состояние и история](STATUS.md).
