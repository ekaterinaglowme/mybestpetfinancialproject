# PetBank: JSON-логирование и логирование всех HTTP-запросов

Дата: 2026-06-18
Ветка: новая ветка от `main`

## Контекст

PetBank — сервис на FastAPI в одном файле `server.py`. Сейчас логирование
настроено через `logging.basicConfig` в человекочитаемом текстовом формате
(`%(asctime)s | %(levelname)-8s | %(message)s`), а бизнес-события (приём заявки,
причины отказа, итог) пишутся вручную внутри `make_decision`. Логирования самих
HTTP-запросов нет.

Прод крутится в Docker; логи смотрятся через `docker logs petbank`. Для удобного
сбора и поиска логи нужны в структурированном виде (JSON), а не в виде свободного
текста.

## Цели

1. Все логи приложения пишутся в **JSON** — одна JSON-строка на событие в stdout.
2. Логируется **каждый HTTP-запрос**: метаданные (метод, путь, статус-код,
   длительность, IP клиента, `request_id`) **+ полное тело запроса**.
3. Все логи одного запроса связаны общим `request_id` (включая существующие
   бизнес-логи в `make_decision`) — без ручного проброса id через сигнатуры.
4. Новых runtime-зависимостей не добавляется (только стандартная библиотека +
   middleware, идущий в составе FastAPI/Starlette).

## Вне объёма

- Аутентификация / авторизация, скрытие или маскирование ПДн (по явному решению
  логируем полное тело как есть — см. «Безопасность»).
- Ротация логов, запись в файлы, отправка во внешние системы (агрегатор сам
  собирает stdout из Docker).
- Изменение бизнес-правил и схемы ответа.
- Логирование тела **ответа** (логируем только статус-код ответа).

## Безопасность (ПДн)

Тело запроса содержит персональные данные (ФИО, телефон, дата рождения) и по
решению заказчика пишется в лог **в открытом виде**. Это осознанный выбор. В
спецификации фиксируем последствие: к хранилищу логов должен быть ограничен
доступ, а срок хранения — ограничен. Технических мер маскирования в этом объёме
не делаем; при необходимости маскирование можно добавить позже в одном месте
(сборка поля `body` в middleware).

## Архитектура

### Новый модуль `logging_setup.py`

Логику логирования выносим из `server.py` (он уже ~210 строк) в отдельный модуль.

**`JsonFormatter(logging.Formatter)`** — сериализует `LogRecord` в одну строку
JSON. Поля каждой записи:

| Поле | Источник |
|---|---|
| `timestamp` | время записи, ISO-8601 в UTC (напр. `2026-06-18T12:34:56.789Z`) |
| `level` | `record.levelname` |
| `logger` | `record.name` |
| `message` | отрендеренное `record.getMessage()` |
| `request_id` | из записи (см. ниже); опускается, если пустой |
| `exc_info` | строка трейсбэка, если есть исключение |
| прочие | любые structured-поля, переданные через `extra={...}` |

Сериализация — `json.dumps(..., ensure_ascii=False, default=str)` (кириллица
читаемой, нестандартные типы вроде `date` приводятся к строке).

**Инъекция `request_id` через record factory.** Чтобы поле попадало в **каждую**
запись независимо от логгера и хендлера (в т.ч. в записи uvicorn и в тесты под
`caplog`), используем `logging.setLogRecordFactory`: фабрика читает `request_id`
из `contextvars.ContextVar` и кладёт в `record.request_id`. Вне запроса — пустая
строка (формат опустит поле).

**`setup_logging()`** — идемпотентно:
- ставит на root-логгер один помеченный `StreamHandler(sys.stdout)` с
  `JsonFormatter` (предварительно убрав только свой ранее добавленный хендлер по
  метке, не трогая чужие — напр. хендлер `caplog` в тестах), уровень `INFO`;
- ставит record factory с инъекцией `request_id`.

Логгеры uvicorn (`uvicorn`, `uvicorn.error`) при `log_config=None` (см. ниже)
не имеют своих хендлеров и всплывают в root → форматируются тем же JSON.

### Middleware логирования запросов

Регистрируется в `server.py` через `@app.middleware("http")`. Имя логгера —
`petbank.access`. Алгоритм на каждый запрос:

1. `request_id` = заголовок `X-Request-ID` из запроса, иначе новый `uuid4().hex`.
   Кладётся в `ContextVar` (токен сохраняется для сброса в конце).
2. Чтение тела: `body_bytes = await request.body()` (Starlette кэширует тело, так
   что эндпоинт затем читает его повторно без проблем). Обрезка до
   `MAX_BODY_BYTES = 10240`. Парсинг как JSON; при ошибке — сырой текст
   (`decode(errors="replace")`, обрезанный). Пустое тело → поле `body` опускается.
3. Замер времени `time.perf_counter()`.
4. `response = await call_next(request)`; при исключении — статус считается `500`,
   исключение пробрасывается дальше после записи лога.
5. В ответ добавляется заголовок `X-Request-ID`.
6. Пишется **одна** запись уровня INFO (при 5xx/исключении — с `exc_info`):
   `event="http_request"`, `method`, `path`, `query`, `status_code`,
   `duration_ms` (округл. до 0.1 мс), `client_ip` (`request.client.host`), `body`.
7. В `finally` сбрасывается `ContextVar`.

### Корреляция с бизнес-логами

Существующие вызовы `logger.info(...)` в `make_decision` менять по сигнатуре не
нужно — `request_id` подставит record factory. Дополнительно итоговый лог решения
обогащается structured-полями: `logger.info("...итог: %s", ..., extra={
"application_id": application_id, "status": status})`, чтобы по логам можно было
фильтровать решения. `application_id` (бизнес-id заявки) и `request_id`
(технический id HTTP-запроса) сосуществуют.

### Настройка uvicorn (`server.py`)

В `run()` вызывается `setup_logging()` перед стартом, а `uvicorn.run(...)`
получает `log_config=None` (не перетирать нашу конфигурацию логирования) и
`access_log=False` (единственный источник access-логов — наше middleware).

`setup_logging()` также вызывается на уровне импорта модуля `server` (один раз),
чтобы JSON-логирование и инъекция `request_id` работали и под тестами, которые
импортируют `app` напрямую, не вызывая `run()`.

## Формат логов (пример)

Бизнес-лог и access-лог одной заявки, связанные общим `request_id`:

```json
{"timestamp":"2026-06-18T12:34:56.781Z","level":"INFO","logger":"server","message":"Заявка 3f2a… — итог: APPROVED","request_id":"7c9e…","application_id":"3f2a…","status":"approved"}
{"timestamp":"2026-06-18T12:34:56.789Z","level":"INFO","logger":"petbank.access","message":"POST /applications 200","request_id":"7c9e…","event":"http_request","method":"POST","path":"/applications","query":"","status_code":200,"duration_ms":12.4,"client_ip":"172.18.0.1","body":{"last_name":"Иванов","first_name":"Иван","phone":"+79991234567","birth_date":"2000-05-15","country":"Россия","amount":100000}}
```

## Тесты

Новый файл `tests/test_logging.py` (pytest + `TestClient`, как в `test_http.py`):

- **`JsonFormatter`**: запись с `extra` сериализуется в валидный JSON, содержит
  ожидаемые ключи (`timestamp`, `level`, `logger`, `message`); кириллица не
  экранируется; `request_id` опускается, когда пуст.
- **Middleware — метаданные**: после `POST /applications` в логах есть запись
  `event="http_request"` с верными `method`, `path`, `status_code`, числовым
  `duration_ms`; в ответе присутствует заголовок `X-Request-ID`.
- **Middleware — тело**: записанное `body` содержит отправленные поля (включая
  ПДн — проверяем, что полное тело логируется).
- **Битый JSON**: `POST` с телом `b"{not json"` и `Content-Type: application/json`
  → ответ остаётся `422`, а в `body` лога лежит сырой текст (фоллбэк), запись не
  падает.
- **Корреляция**: бизнес-лог из `make_decision` и access-лог одного запроса имеют
  одинаковый `request_id` (читаем `record.request_id` через `caplog`).
- **Переданный `X-Request-ID`**: входящий заголовок переиспользуется в логах и в
  ответе.

Существующие тесты (`test_decision.py`, `test_http.py`) должны остаться зелёными —
middleware не меняет коды ответов и тела ответов.

## Файлы

| Файл | Действие |
|---|---|
| `logging_setup.py` | **Новый**: `JsonFormatter`, record factory с `request_id`, `setup_logging()`, `ContextVar` |
| `server.py` | Убрать `logging.basicConfig`; вызвать `setup_logging()` (импорт + в `run()`); добавить middleware логирования запросов; `uvicorn.run(log_config=None, access_log=False)`; `extra` в итоговом логе решения |
| `tests/test_logging.py` | **Новый**: тесты форматтера, middleware, корреляции |
| `requirements.txt` | Не трогать (новых зависимостей нет) |
| `README.md` | Кратко упомянуть формат логов и `request_id` (опционально) |

## Зависимости

Новых нет. Используются `logging`, `json`, `contextvars`, `time`, `uuid` из
стандартной библиотеки и middleware из состава FastAPI/Starlette.
