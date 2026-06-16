# PetBank: Pydantic + Swagger UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual body parsing and `validate_payload` with Pydantic models so FastAPI auto-generates a working Swagger UI at `/docs`.

**Architecture:** Add `ApplicationRequest` and `ApplicationDecision` Pydantic models to `server.py`; remove `validate_payload` and manual JSON parsing; update `make_decision` to accept `ApplicationRequest` directly; update tests to reflect that validation errors are now `422` (FastAPI/Pydantic standard) instead of custom `400`.

**Tech Stack:** FastAPI, Pydantic v2, pytest, fastapi.testclient.TestClient, Python 3.10+

---

## File Map

| File | Изменение |
|---|---|
| `server.py` | Добавить Pydantic-модели, удалить `validate_payload` и ручной парсинг, упростить эндпоинт |
| `tests/test_decision.py` | Заменить хелперы и тесты `validate_payload` на тесты `ApplicationRequest`; обновить тесты `make_decision` |
| `tests/test_http.py` | Изменить 2 теста: `400 → 422` для ошибок валидации и невалидного JSON |

---

### Task 1: Новая ветка

**Files:** —

- [ ] **Step 1: Создать ветку от текущей**

```bash
git checkout -b feat/pydantic-swagger
git branch --show-current
```

Expected: `feat/pydantic-swagger`

---

### Task 2: Pydantic-модели + обновление server.py и test_decision.py

**Files:**
- Modify: `server.py`
- Modify: `tests/test_decision.py`

Сначала пишем новые тесты (они упадут, так как `ApplicationRequest` ещё не существует), затем реализуем.

- [ ] **Step 1: Полностью заменить `tests/test_decision.py`**

```python
"""Юнит-тесты бизнес-логики: возраст, валидация, решение.

Чистые функции из server.py, без HTTP — гоняются мгновенно.
"""

from datetime import date, timedelta

import pytest

from server import MAX_AGE, MIN_AGE, ApplicationRequest, calculate_age, make_decision


# --- calculate_age ---------------------------------------------------------

def test_age_birthday_already_passed():
    # ДР в этом году уже прошёл.
    assert calculate_age(date(1990, 1, 1), date(2026, 6, 8)) == 36


def test_age_birthday_not_yet():
    # ДР в этом году ещё впереди — год вычитается.
    assert calculate_age(date(1990, 12, 31), date(2026, 6, 8)) == 35


def test_age_birthday_today():
    assert calculate_age(date(2000, 6, 8), date(2026, 6, 8)) == 26


def test_age_leap_day_before_feb29():
    # Рождён 29 февраля, «не-високосный» год, день ещё не наступил.
    assert calculate_age(date(2004, 2, 29), date(2026, 2, 28)) == 21


def test_age_leap_day_on_mar1():
    assert calculate_age(date(2004, 2, 29), date(2026, 3, 1)) == 22


# --- ApplicationRequest validation -----------------------------------------

def _valid_request(**overrides) -> ApplicationRequest:
    data = {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        "phone": "+79991234567",
        "birth_date": "2000-05-15",
        "country": "Россия",
        "amount": 100000,
    }
    data.update(overrides)
    return ApplicationRequest.model_validate(data)


def test_request_valid():
    req = _valid_request()
    assert req.last_name == "Иванов"
    assert req.birth_date == date(2000, 5, 15)
    assert req.amount == 100000


def test_request_body_not_dict():
    with pytest.raises(Exception):
        ApplicationRequest.model_validate(["not", "a", "dict"])


@pytest.mark.parametrize("field", ["last_name", "first_name", "phone", "country"])
def test_request_required_string_missing(field):
    data = {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "birth_date": "2000-05-15",
        "country": "Россия",
    }
    del data[field]
    with pytest.raises(Exception):
        ApplicationRequest.model_validate(data)


@pytest.mark.parametrize("field", ["last_name", "first_name", "phone", "country"])
def test_request_required_string_blank(field):
    with pytest.raises(Exception):
        _valid_request(**{field: "   "})


def test_request_strips_whitespace():
    req = _valid_request(first_name="  Иван  ")
    assert req.first_name == "Иван"


def test_request_middle_name_optional():
    req = ApplicationRequest.model_validate({
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "birth_date": "2000-05-15",
        "country": "Россия",
    })
    assert req.middle_name == ""


def test_request_middle_name_wrong_type():
    with pytest.raises(Exception):
        _valid_request(middle_name=123)


def test_request_birth_date_bad_format():
    with pytest.raises(Exception):
        _valid_request(birth_date="15.05.2000")


def test_request_birth_date_in_future():
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(Exception):
        _valid_request(birth_date=future)


def test_request_amount_optional():
    req = ApplicationRequest.model_validate({
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "birth_date": "2000-05-15",
        "country": "Россия",
    })
    assert req.amount is None


def test_request_amount_bool_rejected():
    with pytest.raises(Exception):
        _valid_request(amount=True)


def test_request_amount_negative_rejected():
    with pytest.raises(Exception):
        _valid_request(amount=-1)


# --- make_decision ---------------------------------------------------------

def _valid_decision_request(birth_date: date, country: str = "Россия") -> ApplicationRequest:
    return ApplicationRequest(
        last_name="Иванов",
        first_name="Иван",
        middle_name="Иванович",
        phone="+79991234567",
        country=country,
        birth_date=birth_date,
    )


def test_decision_adult_approved():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_valid_decision_request(born))
    assert result["status"] == "approved"
    assert result["reasons"] == []
    assert result["applicant"]["age"] == 30


def test_decision_minor_declined():
    born = date.today().replace(year=date.today().year - 10)
    result = make_decision(_valid_decision_request(born))
    assert result["status"] == "declined"
    assert len(result["reasons"]) == 1
    assert str(MIN_AGE) in result["reasons"][0]


def test_decision_full_name_with_middle():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_valid_decision_request(born))
    assert result["applicant"]["full_name"] == "Иванов Иван Иванович"


def test_decision_full_name_without_middle():
    born = date.today().replace(year=date.today().year - 30)
    req = ApplicationRequest(
        last_name="Иванов",
        first_name="Иван",
        phone="+79991234567",
        country="Россия",
        birth_date=born,
    )
    result = make_decision(req)
    assert result["applicant"]["full_name"] == "Иванов Иван"


def test_decision_has_application_id():
    result = make_decision(_valid_decision_request(date(2000, 5, 15)))
    assert result["application_id"]


def test_decision_max_age_boundary_approved():
    born = date.today().replace(year=date.today().year - MAX_AGE)
    result = make_decision(_valid_decision_request(born))
    assert result["status"] == "approved"
    assert result["reasons"] == []
    assert result["applicant"]["age"] == MAX_AGE


def test_decision_over_max_age_declined():
    born = date.today().replace(year=date.today().year - (MAX_AGE + 1))
    result = make_decision(_valid_decision_request(born))
    assert result["status"] == "declined"
    assert any(str(MAX_AGE) in reason for reason in result["reasons"])


def test_decision_blocked_country_declined():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_valid_decision_request(born, country="Китай"))
    assert result["status"] == "declined"
    assert any("Китай" in reason for reason in result["reasons"])


def test_decision_blocked_country_case_insensitive():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_valid_decision_request(born, country="китай"))
    assert result["status"] == "declined"
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают на импорте**

```bash
.venv/bin/pytest tests/test_decision.py -q 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'ApplicationRequest' from 'server'`

- [ ] **Step 3: Полностью заменить `server.py`**

```python
"""PetBank — простейший сервер приёма заявок.

Запуск:  python server.py   (или  python server.py 8080  — другой порт)

Эндпоинты:
    POST /applications  — подать заявку, вернёт решение approved / declined
    GET  /health        — проверка, что сервер жив
    GET  /              — короткая справка
    GET  /docs          — Swagger UI (интерактивная документация)
"""

import os
import sys
import uuid
from datetime import date, datetime

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, field_validator

# --- Бизнес-правила --------------------------------------------------------

# Заявителю должно быть от MIN_AGE до MAX_AGE лет включительно.
MIN_AGE = 18
MAX_AGE = 35

# Страны, заявки из которых не принимаются (сравнение без учёта регистра).
BLOCKED_COUNTRIES = {"китай"}


# --- Pydantic-модели -------------------------------------------------------

class ApplicationRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "last_name": "Иванов",
            "first_name": "Иван",
            "middle_name": "Иванович",
            "phone": "+79991234567",
            "birth_date": "2000-05-15",
            "country": "Россия",
            "amount": 100000,
        }
    })

    last_name: str
    first_name: str
    middle_name: str = ""
    phone: str
    birth_date: date
    country: str
    amount: float | None = None

    @field_validator("last_name", "first_name", "phone", "country", mode="before")
    @classmethod
    def strip_and_require_nonempty(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("Обязательное поле: непустая строка")
        stripped = v.strip()
        if not stripped:
            raise ValueError("Обязательное поле: непустая строка")
        return stripped

    @field_validator("middle_name", mode="before")
    @classmethod
    def strip_middle_name(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("Должно быть строкой")
        return v.strip()

    @field_validator("birth_date", mode="after")
    @classmethod
    def birth_date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Дата рождения не может быть в будущем")
        return v

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v: object) -> "float | None":
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError("Должно быть неотрицательным числом")
        if not isinstance(v, (int, float)):
            raise ValueError("Должно быть неотрицательным числом")
        if v < 0:
            raise ValueError("Должно быть неотрицательным числом")
        return float(v)


class ApplicantInfo(BaseModel):
    full_name: str
    age: int
    phone: str


class ApplicationDecision(BaseModel):
    application_id: str
    status: str
    applicant: ApplicantInfo
    reasons: list[str]
    received_at: str


# --- Бизнес-логика ---------------------------------------------------------

def calculate_age(birth_date: date, today: date) -> int:
    """Полное число лет на дату `today`."""
    years = today.year - birth_date.year
    # День рождения в этом году ещё не наступил — вычитаем год.
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def make_decision(payload: ApplicationRequest) -> dict:
    """Принимает решение по провалидированной заявке."""
    today = date.today()
    age = calculate_age(payload.birth_date, today)

    reasons = []
    if age < MIN_AGE:
        reasons.append(f"Возраст заявителя {age} лет — меньше минимально допустимого {MIN_AGE}")
    if age > MAX_AGE:
        reasons.append(f"Возраст заявителя {age} лет — больше макс допустимого {MAX_AGE}")
    if payload.country.lower() in BLOCKED_COUNTRIES:
        reasons.append(f"Заявки из страны «{payload.country}» не принимаются")

    status = "approved" if not reasons else "declined"
    full_name = " ".join(
        part for part in (payload.last_name, payload.first_name, payload.middle_name) if part
    )

    return {
        "application_id": str(uuid.uuid4()),
        "status": status,
        "applicant": {
            "full_name": full_name,
            "age": age,
            "phone": payload.phone,
        },
        "reasons": reasons,
        "received_at": datetime.now().isoformat(timespec="seconds"),
    }


# --- HTTP-слой -------------------------------------------------------------

app = FastAPI(title="PetBank")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "service": "PetBank",
        "endpoints": ["POST /applications", "GET /health", "GET /docs"],
        "rule": f"возраст {MIN_AGE}-{MAX_AGE}, страна не в стоп-листе",
    }


@app.post("/applications", response_model=ApplicationDecision)
async def create_application(payload: ApplicationRequest):
    return make_decision(payload)


def run(host="0.0.0.0", port=None):
    port = port or int(os.environ.get("PORT", "8000"))
    print(f"PetBank запущен: http://localhost:{port}  (Ctrl+C — остановить)")
    print(f"Swagger UI:       http://localhost:{port}/docs")
    print(
        f"Правило одобрения: возраст {MIN_AGE}-{MAX_AGE} лет, "
        f"страна не в стоп-листе ({', '.join(sorted(BLOCKED_COUNTRIES))})"
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(port=cli_port)
```

- [ ] **Step 4: Запустить тесты test_decision.py — должны пройти**

```bash
.venv/bin/pytest tests/test_decision.py -v 2>&1 | tail -20
```

Expected: 33 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_decision.py
git commit -m "feat: replace validate_payload with Pydantic models, expose Swagger UI"
```

---

### Task 3: Обновить test_http.py

**Files:**
- Modify: `tests/test_http.py`

После миграции на Pydantic FastAPI возвращает `422` вместо наших кастомных `400` для ошибок валидации и невалидного JSON. Нужно обновить 2 теста.

- [ ] **Step 1: Заменить `test_application_validation_error`**

Было:
```python
def test_application_validation_error(client):
    resp = client.post("/applications", json={"first_name": "Иван"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "validation_error"
    assert body["details"]
```

Стало:
```python
def test_application_validation_error(client):
    resp = client.post("/applications", json={"first_name": "Иван"})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "last_name" in fields_with_errors
```

- [ ] **Step 2: Заменить `test_application_invalid_json`**

Было:
```python
def test_application_invalid_json(client):
    resp = client.post(
        "/applications",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"
```

Стало:
```python
def test_application_invalid_json(client):
    resp = client.post(
        "/applications",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 3: Запустить все тесты — должны пройти**

```bash
.venv/bin/pytest -q
```

Expected: `41 passed, 1 warning`

- [ ] **Step 4: Commit**

```bash
git add tests/test_http.py
git commit -m "test: update validation error assertions to 422 (Pydantic)"
```

---

### Task 4: Проверить Swagger UI вручную

**Files:** —

- [ ] **Step 1: Запустить сервер**

```bash
.venv/bin/python server.py &
```

Expected в консоли:
```
PetBank запущен: http://localhost:8000  (Ctrl+C — остановить)
Swagger UI:       http://localhost:8000/docs
```

- [ ] **Step 2: Проверить /docs**

```bash
curl -s http://localhost:8000/docs | grep -c "swagger"
```

Expected: число больше 0 (страница возвращает HTML со Swagger)

- [ ] **Step 3: Проверить /openapi.json содержит схему ApplicationRequest**

```bash
curl -s http://localhost:8000/openapi.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
schema = d['components']['schemas']['ApplicationRequest']
print('required:', schema['required'])
print('properties:', list(schema['properties'].keys()))
"
```

Expected:
```
required: ['last_name', 'first_name', 'phone', 'birth_date', 'country']
properties: ['last_name', 'first_name', 'middle_name', 'phone', 'birth_date', 'country', 'amount']
```

- [ ] **Step 4: Остановить сервер**

```bash
kill $(lsof -ti :8000)
```
