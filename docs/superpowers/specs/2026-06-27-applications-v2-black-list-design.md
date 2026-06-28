# Дизайн: ручка `POST /applications/v2` с проверкой паспорта по чёрному списку

Дата: 2026-06-27
Ветка: `feat/applications-v2-black-list`

## Цель

Перед выдачей кредита проверять паспорт заявителя по внешнему сервису чёрного
списка. Текущая ручка `POST /applications` (v1) остаётся без изменений; добавляется
вторая версия `POST /applications/v2` с расширенным набором полей и упрощённым
набором правил отказа.

## Внешний сервис чёрного списка

Сервис «СтопЛист» (OpenAPI: `http://212.147.238.3:8090/openapi.json`):

- `GET /check?passport=<строка>` → `200 {"passport": "<эхо>", "in_terror_list": bool}`
- `GET /health` → `200`
- `422` при отсутствии параметра `passport`.

Поле ответа `in_terror_list` — это API сервиса; в нашем коде вся терминология
называется **`black_list`**. `True` означает «паспорт в чёрном списке → отказ».

## Контекст кодовой базы (origin/main)

- Код в `app/src/` (`pythonpath = ["app/src"]`, импорты плоские: `import server`,
  `from db import ...`).
- `make_decision(payload)` в `app/src/server.py` — текущая логика v1
  (возраст 18–35, `BLOCKED_COUNTRIES = {"китай"}`).
- Персистентность: `app/src/models.py` (`User` 1:N `Application`),
  `app/src/repository.py` (`get_or_create_user`, `save_application`),
  `app/src/db.py`. `Application.country` сейчас `String` **NOT NULL**.
- Мидлвары `app/src/request_timeout.py` и `app/src/ratelimit.py` привязаны к
  точному пути `/applications` (точное сравнение `request.url.path`).
- Глобальный request-timeout ≈ 1с (env `REQUEST_TIMEOUT_SECONDS`).
- Alembic: единственная ревизия `alembic/versions/0001_initial.py` → новая `0002`.
- `httpx` в `requirements.txt` отсутствует (есть только транзитивно для тестов).

## Бизнес-правила v2

Заявка **отклоняется** (`status="declined"`), если выполнено хотя бы одно:

1. **Возраст < 18 лет** — причина «Возраст заявителя N лет — меньше минимально
   допустимого 18», метрика `REJECTION_REASONS{reason="age_below_min"}`.
2. **Паспорт в чёрном списке** — причина «Паспорт в чёрном списке», метрика
   `reason="black_list"`.
3. **Проверка чёрного списка не удалась** (fail-closed) — причина «Не удалось
   проверить паспорт по чёрному списку — заявка отклонена», метрика
   `reason="black_list_check_unavailable"`.

Иначе — `status="approved"`.

Никаких других правил у v2 нет: верхняя граница возраста (35) и фильтр по стране
к v2 **не применяются**. v1 сохраняет прежние правила (18–35 + стоп-страны).

## Поля запроса v2 (`ApplicationRequestV2`)

Общие с v1 (через общий базовый класс): `last_name`, `first_name`, `middle_name`,
`phone`, `birth_date`, `amount` — с теми же валидаторами, что сейчас.

Новые поля v2 (без `country`):

| Поле           | Тип / правила                                              |
|----------------|-----------------------------------------------------------|
| `email`        | строка, валидация лёгким regex (есть `@`, домен с точкой), strip, обязательно |
| `passport`     | строка, strip, непустая, обязательно                      |
| `region`       | строка, strip, непустая, обязательно                      |
| `loan_purpose` | `Literal["покупка", "перекредитование"]`, обязательно     |

Любое нарушение → `422`. Жёсткого формата паспорта не навязываем (сервис —
эхо-проверка). `email-validator` не тащим — обходимся regex.

## Архитектура

### Рефактор моделей (`app/src/server.py`)

Выделить `ApplicationBase(BaseModel)` с общими полями и валидаторами. Затем:

- `ApplicationRequest(ApplicationBase)` — v1, добавляет `country` (поведение
  байт-в-байт прежнее, тесты v1 зелёные).
- `ApplicationRequestV2(ApplicationBase)` — добавляет `email`, `passport`,
  `region`, `loan_purpose`. Без `country`.

### Новый модуль `app/src/black_list.py`

```python
class BlackListError(Exception): ...

async def check_passport(passport: str) -> bool:
    """True — паспорт в чёрном списке. Бросает BlackListError при любом сбое."""
```

- `httpx.AsyncClient` со своим таймаутом `BLACK_LIST_TIMEOUT_SECONDS` (дефолт
  `0.8`, заметно меньше глобального ~1с — иначе request-timeout мидлвар прибьёт
  весь запрос как 503 вместо чистого fail-closed-отказа).
- URL: `BLACK_LIST_URL` (дефолт `http://212.147.238.3:8090`), путь `/check`,
  query `passport`.
- `raise_for_status`, читаем `in_terror_list`.
- Любой сбой (`httpx.HTTPError`, тайм-аут, кривой/неполный JSON) → `BlackListError`.
- Конструкция клиента вынесена так, чтобы тест мог подменить транспорт
  (`httpx.MockTransport`) без сети и без новых зависимостей.

### Решение v2 (`app/src/server.py`)

Отдельная функция (v1 `make_decision` не трогаем):

```python
def make_decision_v2(payload, *, in_black_list=False,
                     black_list_check_failed=False) -> dict:
```

Считает возраст (переиспользует `calculate_age`), собирает причины по трём
правилам выше, проставляет метрики (`REJECTION_REASONS`, `DECISIONS`,
`APPLICATION_AMOUNT_RUB`), формирует тот же по форме ответ, что v1
(`application_id`, `status`, `applicant`, `reasons`, `received_at`). У v2 нет
страны → метрика `DECISIONS` пишется с label `country="-"`.

### Ручка `POST /applications/v2`

```python
@app.post("/applications/v2", response_model=ApplicationDecision)
async def create_application_v2(payload: ApplicationRequestV2, session=Depends(get_session)):
    try:
        in_black_list = await check_passport(payload.passport)
        check_failed = False
    except BlackListError:
        logger.warning("Чёрный список недоступен — заявка отклонена (fail-closed)")
        in_black_list, check_failed = False, True
    decision = make_decision_v2(payload, in_black_list=in_black_list,
                                black_list_check_failed=check_failed)
    # сохранение в БД — тем же путём, что v1 (get_or_create_user + save_application)
    return decision
```

### Персистентность (Alembic `0002`)

В таблицу `applications` добавить колонки (все nullable — у v1-строк остаются NULL):
`email`, `passport`, `region`, `loan_purpose` (все `String`). `country` сделать
**nullable**.

`save_application` расширить необязательными параметрами `email/passport/region/
loan_purpose` (default `None`), чтобы v1-вызов не менялся. v2 пишет новые поля и
`country=None`.

Ответ ручки форму не меняет — новые поля в ответе не эхо.

### Мидлвары

`install_request_timeout` и `install_rate_limiter` расширить, чтобы они покрывали
и `/applications/v2` (матч по множеству путей вместо точного сравнения одного).

### Зависимости и конфиг

- `httpx>=0.27` → `requirements.txt` (промоут из транзитивной).
- `BLACK_LIST_URL`, `BLACK_LIST_TIMEOUT_SECONDS` → `.env.example`.

## Обработка ошибок

- Чёрный список недоступен/таймаут/5xx/битый JSON → `BlackListError` →
  **fail-closed**: `status="declined"` (HTTP 200), причина `black_list_check_unavailable`.
- Сбой БД при сохранении → `500` (как у v1).
- Невалидные поля запроса → `422`.

## Тестирование

`tests/` (pytest, async, SQLite-фикстура):

- v2: валидная заявка (≥18, чистый паспорт) → `approved`.
- v2: возраст < 18 → `declined`, причина возраста.
- v2: паспорт в чёрном списке (мок `check_passport` → True) → `declined`, причина
  `black_list`.
- v2: сбой чёрного списка (мок бросает `BlackListError`) → `declined`,
  причина `black_list_check_unavailable` (fail-closed).
- v2: отсутствуют/битые `passport` / `email` / `loan_purpose` → `422`.
- v2: после approved-заявки запись с новыми полями есть в БД.
- v1: `POST /applications` без изменений и без вызова чёрного списка.
- Юнит `black_list.check_passport`: через `httpx.MockTransport` — кейсы
  `in_terror_list=true/false`, 5xx, таймаут, битый JSON.

## Вне рамок (YAGNI)

- Кэширование/ретраи запросов к чёрному списку.
- Хранение паспорта в `users` и дедупликация по паспорту (паспорт пишем в
  `applications` как снимок заявки).
- Изменение формы ответа ручки.
