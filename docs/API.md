# 🔌 Справочник API PetBank

Все эндпоинты, схемы запроса/ответа, бизнес-правила, валидация и способы вызвать.
Описывается актуальная ветка `main`.

- **Базовый URL (локально):** `http://localhost:8000`
- **Формат:** JSON (`Content-Type: application/json`)
- **Авторизация:** нет (учебный сервис)
- **Интерактивная документация:** Swagger UI на [`/docs`](http://localhost:8000/docs)

Содержание:

1. [Карта эндпоинтов](#1-карта-эндпоинтов)
2. [POST /applications — подать заявку](#2-post-applications--подать-заявку)
3. [Бизнес-правила решения](#3-бизнес-правила-решения)
4. [Валидация полей](#4-валидация-полей)
5. [Ошибки валидации (422)](#5-ошибки-валидации-422)
6. [Служебные эндпоинты](#6-служебные-эндпоинты)
7. [Как дёрнуть API](#7-как-дёрнуть-api)

---

## 1. Карта эндпоинтов

| Метод | Путь | Назначение | Кто отдаёт |
|---|---|---|---|
| `POST` | `/applications` | Подать заявку, получить решение | наш код |
| `GET` | `/health` | Проверка живости, liveness (`{"status":"ok"}`) | наш код |
| `GET` | `/ready` | Готовность принимать трафик, readiness (503 — БД недоступна) | наш код |
| `GET` | `/` | Короткая справка о сервисе | наш код |
| `GET` | `/metrics` | Метрики в формате Prometheus | instrumentator |
| `GET` | `/docs` | Swagger UI (интерактивная дока) | FastAPI |
| `GET` | `/redoc` | ReDoc (альтернативная дока) | FastAPI |
| `GET` | `/openapi.json` | OpenAPI-схема (генерится из Pydantic) | FastAPI |

---

## 2. POST /applications — подать заявку

Главный эндпоинт. Принимает заявку, синхронно применяет правила и возвращает
решение. Состояние не сохраняется.

### Тело запроса (`ApplicationRequest`)

| Поле | Тип | Обяз. | Описание |
|---|---|:---:|---|
| `last_name` | string | ✅ | Фамилия (непустая) |
| `first_name` | string | ✅ | Имя (непустое) |
| `middle_name` | string | — | Отчество; по умолчанию `""` |
| `phone` | string | ✅ | Телефон (непустой) |
| `birth_date` | string (`YYYY-MM-DD`) | ✅ | Дата рождения; не в будущем |
| `country` | string | ✅ | Страна заявителя (непустая) |
| `amount` | number | — | Запрашиваемая сумма, ≥ 0 |

Пример:

```json
{
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "phone": "+79991234567",
  "birth_date": "2000-05-15",
  "country": "Россия",
  "amount": 100000
}
```

### Ответ 200 (`ApplicationDecision`)

| Поле | Тип | Описание |
|---|---|---|
| `application_id` | string (uuid) | Уникальный id заявки |
| `status` | string | `"approved"` или `"declined"` |
| `applicant.full_name` | string | «Фамилия Имя Отчество» (отчество — если есть) |
| `applicant.age` | integer | Полных лет на сегодня |
| `applicant.phone` | string | Телефон из заявки |
| `reasons` | array[string] | Причины отказа; пусто, если одобрено |
| `received_at` | string (ISO-8601) | Время приёма (до секунд) |

**Одобрено:**

```json
{
  "application_id": "0f4c2c1e-…-uuid",
  "status": "approved",
  "applicant": { "full_name": "Иванов Иван Иванович", "age": 26, "phone": "+79991234567" },
  "reasons": [],
  "received_at": "2026-06-22T16:52:00"
}
```

**Отказано** (возраст вне диапазона и/или страна в стоп-листе):

```json
{
  "application_id": "9a1b…-uuid",
  "status": "declined",
  "applicant": { "full_name": "Ли Вэй", "age": 26, "phone": "+861234567890" },
  "reasons": ["Заявки из страны «Китай» не принимаются"],
  "received_at": "2026-06-22T16:53:10"
}
```

> Важно: **бизнес-отказ — это тоже HTTP 200.** Код 200 означает «заявку
> обработали», а одобрена она или нет — в поле `status`. Не-200 бывает только при
> ошибке валидации входа (422) или несуществующем пути (404).

---

## 3. Бизнес-правила решения

Решение принимает `make_decision()` в `server.py`. Заявка **одобряется только
если не нарушено ни одно правило**. Каждое нарушение добавляет строку в `reasons`.

| # | Правило | Константа | Нарушение → причина |
|---|---|---|---|
| 1 | Возраст ≥ 18 | `MIN_AGE = 18` | `Возраст заявителя N лет — меньше минимально допустимого 18` |
| 2 | Возраст ≤ 35 | `MAX_AGE = 35` | `Возраст заявителя N лет — больше макс допустимого 35` |
| 3 | Страна не в стоп-листе | `BLOCKED_COUNTRIES = {"китай"}` | `Заявки из страны «X» не принимаются` |

Детали:

- **Возраст** считает `calculate_age(birth_date, today)` — полных лет, с учётом,
  наступил ли уже день рождения в этом году (и корректно для 29 февраля).
  Границы **включительны**: ровно 18 и ровно 35 лет — одобрение.
- **Страна** сравнивается без учёта регистра (`country.lower()`), «Китай» и
  «китай» эквивалентны.
- Правила **независимы и суммируются**: если нарушены оба (например, 70-летний из
  Китая) — в `reasons` будет несколько причин.

Где менять правила — см. [CHECKLISTS.md](CHECKLISTS.md#➕-добавить-или-изменить-бизнес-правило).

---

## 4. Валидация полей

Валидацию делает Pydantic-модель `ApplicationRequest` **до** бизнес-логики. Если
вход невалиден — `make_decision` даже не вызывается, сразу 422.

| Поле | Что проверяется |
|---|---|
| `last_name`, `first_name`, `phone`, `country` | Должна быть строка; пробелы по краям обрезаются; пустая после обрезки → ошибка; отсутствует → ошибка |
| `middle_name` | Необязательно; `null`/отсутствие → `""`; не-строка → ошибка; пробелы обрезаются |
| `birth_date` | Обязательна; формат строго `YYYY-MM-DD`; дата в будущем → ошибка |
| `amount` | Необязательно; `null`/отсутствие → `null`; `bool`, не-число или отрицательное → ошибка; иначе приводится к `float` |

> Граничные случаи зафиксированы тестами в `tests/test_decision.py`
> (`test_request_*`): обрезка пробелов, пустые строки, неверный формат даты,
> будущая дата, `amount` = `True`/отрицательное и т.д.

---

## 5. Ошибки валидации (422)

При невалидном теле FastAPI/Pydantic возвращает **HTTP 422** в стандартном
формате (не наш кастомный):

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "last_name"],
      "msg": "Field required",
      "input": { "first_name": "Иван" }
    }
  ]
}
```

- `loc` — путь до проблемного поля (последний элемент — имя поля).
- Невалидный JSON в теле (например `{not json`) — тоже **422**.
- Несуществующий путь — **404** `{"detail":"Not Found"}`.

> Исторически (до миграции на Pydantic) ошибки были `400` с телом
> `{"error":"validation_error","details":[…]}`. Сейчас это **422** — учитывай,
> если где-то остался старый клиент. Контракт `openapi.yaml` тоже описывает старый
> 400 и **отстаёт** от кода (см. [STATUS.md](STATUS.md#известные-пробелы-и-техдолг)).

---

## 6. Служебные эндпоинты

### GET /health

Проверка живости (liveness): процесс жив и отвечает, зависимости НЕ проверяет.
Используется Docker HEALTHCHECK и health-check в CI после деплоя.

```
GET /health   →   200   {"status": "ok"}
```

### GET /ready

Готовность принимать трафик (readiness): БД отвечает на `SELECT 1` за таймаут
`DB_READY_TIMEOUT_SECONDS` (по умолчанию 2с). Смысл разделения: на провале
liveness инстанс рестартуют, на провале readiness — уводят трафик (рестарт
приложения при лежащей БД ничем не помог бы).

```
GET /ready   →   200   {"status": "ready"}       БД отвечает
GET /ready   →   503   {"status": "not ready"}   БД недоступна/висит
```

### GET /

Короткая справка о сервисе:

```json
{
  "service": "PetBank",
  "endpoints": ["POST /applications", "GET /health", "GET /docs"],
  "rule": "возраст 18-35, страна не в стоп-листе"
}
```

### GET /metrics

Метрики в текстовом формате Prometheus (`Content-Type: text/plain`). Без
авторизации. Содержит:

- **HTTP-метрики (RED)** — `http_requests_total{…}`,
  `http_request_duration_seconds_bucket/count/sum` и т.п.;
- **Бизнес-метрики** — `petbank_decisions_total{status,country}`,
  `petbank_rejection_reasons_total{reason}`,
  `petbank_application_amount_rub_bucket/count/sum`, `petbank_app_info{version,commit}`.

Подробности — [ARCHITECTURE.md → Observability](ARCHITECTURE.md#метрики-prometheus).

### GET /docs, /redoc, /openapi.json

Авто-документация от FastAPI, генерится из Pydantic-моделей. `/docs` — Swagger UI
с кнопкой **Try it out** (можно отправлять заявки прямо из браузера).

---

## 7. Как дёрнуть API

### curl

```bash
# Одобрят (возраст в диапазоне, страна разрешена)
curl -X POST http://localhost:8000/applications \
  -H "Content-Type: application/json" \
  -d '{"last_name":"Иванов","first_name":"Иван","phone":"+79991234567","birth_date":"2000-05-15","country":"Россия"}'

# Откажут (страна в стоп-листе)
curl -X POST http://localhost:8000/applications \
  -H "Content-Type: application/json" \
  -d '{"last_name":"Ли","first_name":"Вэй","phone":"+861234567890","birth_date":"2000-05-15","country":"Китай"}'

# Живость
curl http://localhost:8000/health
```

### PowerShell

```powershell
$body = @{ last_name="Иванов"; first_name="Иван"; phone="+79991234567"; birth_date="2000-05-15"; country="Россия" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/applications -Method Post -ContentType "application/json" -Body $body
```

### PyCharm / VS Code

Открой [`requests.http`](../requests.http) и жми ▶ над нужным запросом — там уже
есть готовые примеры (одобрение, отказ по возрасту, отказ по стране, ошибка
валидации).

### Swagger UI

Открой `http://localhost:8000/docs` → раскрой `POST /applications` → **Try it
out** → подставь тело → **Execute**. Удобно проверять руками без curl.

### Postman

Импортируй контракт [`openapi.yaml`](../openapi.yaml) (*Import → File*) — Postman
соберёт коллекцию с примерами. Учти: `openapi.yaml` слегка отстаёт от кода
(описывает старый формат ошибок 400). Самая точная схема — `/openapi.json` с
живого сервера.

---

Дальше: [архитектура](ARCHITECTURE.md) · [запуск и эксплуатация](OPERATIONS.md) ·
[чек-листы](CHECKLISTS.md).
