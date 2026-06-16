# PetBank: Swagger UI + миграция на Pydantic

Дата: 2026-06-16
Ветка: новая ветка от `main` (после мёржа `add-old-age-condition`)

## Контекст

PetBank — учебный сервис на FastAPI. Сейчас валидация входящих заявок сделана
вручную через функцию `validate_payload`, а документация поддерживается отдельно
в `openapi.yaml`. Swagger UI на `/docs` есть, но показывает пустую схему для
`POST /applications` — потому что FastAPI не знает о полях (тело парсится вручную).

## Цели

1. Swagger UI на `/docs` показывает полную схему эндпоинта `/applications` с
   полями, типами, примерами и кнопкой «Try it out» — без ручного обновления
   `openapi.yaml`.
2. При добавлении нового поля в `ApplicationRequest` Swagger обновляется
   автоматически — единственный источник правды это код.

## Вне объёма

- Логирование (отложено).
- Аутентификация / авторизация.
- Кастомная HTML-форма.
- Изменение бизнес-правил (`MIN_AGE`, `MAX_AGE`, `BLOCKED_COUNTRIES`).
- `openapi.yaml` и `requests.http` остаются в репозитории как есть, просто
  перестают быть источником правды для документации.

## Архитектура

### Pydantic-модели (`server.py`)

Заменяем `validate_payload` двумя Pydantic-моделями.

**Модель запроса `ApplicationRequest`:**

```python
class ApplicationRequest(BaseModel):
    last_name: str
    first_name: str
    middle_name: str = ""
    phone: str
    birth_date: date
    country: str
    amount: float | None = None
```

Валидаторы:
- `last_name`, `first_name`, `phone`, `country` — обрезать пробелы, отклонить
  пустую строку после обрезки.
- `birth_date` — отклонить дату в будущем.
- `amount` — отклонить отрицательное значение и `bool` (bool — подкласс int,
  нужно отсечь явно).
- `middle_name` — необязательное, обрезать пробелы, по умолчанию `""`.

**Модель ответа `ApplicationDecision`:**

```python
class ApplicantInfo(BaseModel):
    full_name: str
    age: int
    phone: str

class ApplicationDecision(BaseModel):
    application_id: str
    status: str  # "approved" | "declined"
    applicant: ApplicantInfo
    reasons: list[str]
    received_at: str
```

### Изменения в эндпоинте

`POST /applications` принимает `ApplicationRequest` как тело запроса и возвращает
`ApplicationDecision`. FastAPI автоматически:
- парсит и валидирует тело;
- генерирует OpenAPI-схему с полями, типами и примерами;
- отдаёт её в Swagger UI на `/docs`.

`make_decision` получает объект `ApplicationRequest` напрямую вместо `cleaned`-словаря.
`calculate_age` остаётся без изменений.
`validate_payload` удаляется полностью.

### Формат ошибок валидации (breaking change)

| | До | После |
|---|---|---|
| HTTP-код | `400` | `422` |
| Формат | `{"error": "validation_error", "message": "...", "details": [...]}` | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` |

Это стандартный формат FastAPI/Pydantic. Для учебного проекта приемлемо.
Ошибки `bad_request` (пустое тело, невалидный JSON) больше не нужны — FastAPI
обрабатывает их сам и отдаёт 422.

### Swagger UI

FastAPI автоматически отдаёт `/docs` (Swagger UI) и `/openapi.json` из
Pydantic-моделей. Дополнительной настройки не требуется — `/docs` начнёт
показывать полную схему сразу после миграции на Pydantic.

Примеры в Swagger (для кнопки «Try it out») добавляются через
`model_config = ConfigDict(json_schema_extra={"example": {...}})` в модели.

## Тесты

### `tests/test_decision.py`

- Удалить тесты `test_validate_*` — они тестировали `validate_payload`, которой
  больше нет. Валидация теперь покрывается тестами через Pydantic напрямую
  (`ApplicationRequest.model_validate(...)`) или через HTTP.
- Хелпер `_valid_payload()` заменяется на `_valid_request()` возвращающий
  `ApplicationRequest`.
- Хелпер `_cleaned()` удаляется — `make_decision` теперь принимает
  `ApplicationRequest`.
- Тесты `make_decision` и `calculate_age` остаются, только обновляются вызовы.

### `tests/test_http.py`

- Тесты на 400 `validation_error` заменяются на 422.
- Тесты на пустое тело и невалидный JSON — заменяются на 422 (FastAPI).
- Остальные тесты (health, root, approved, declined по возрасту/стране, 404)
  остаются без изменений по смыслу.

## Файлы

| Файл | Действие |
|---|---|
| `server.py` | Добавить Pydantic-модели, удалить `validate_payload`, обновить эндпоинт |
| `tests/test_decision.py` | Переписать хелперы и `validate_*` тесты |
| `tests/test_http.py` | Обновить коды ответов для ошибок валидации |
| `openapi.yaml` | Не трогать (остаётся как legacy-документ) |
| `requests.http` | Не трогать |
| `README.md` | Не трогать |
