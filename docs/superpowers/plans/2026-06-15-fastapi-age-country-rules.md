# FastAPI + правила «возраст 18–35» и «страна» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в PetBank обязательное поле `country` со стоп-листом стран,
закрепить правило `MAX_AGE = 35` юнит-тестами и перевести HTTP-слой сервера с
`http.server` на FastAPI + Uvicorn без изменения внешнего контракта API.

**Architecture:** Бизнес-логика (`calculate_age`, `validate_payload`,
`make_decision`) остаётся набором чистых функций в `server.py`, к ним
добавляется проверка страны. HTTP-слой переписывается на FastAPI: один
POST-эндпоинт читает тело запроса вручную (как раньше), чтобы сохранить
точные коды/форматы ответов, остальные эндпоинты — простые обработчики.
`main.py` не меняется.

**Tech Stack:** Python 3.14, FastAPI, Uvicorn, pytest, httpx (для
`fastapi.testclient.TestClient`).

Ветка: `add-old-age-condition` (уже создана, деплой триггерится только на
`main`, так что промежуточные коммиты безопасны).

Спецификация: `docs/superpowers/specs/2026-06-15-fastapi-age-country-rules-design.md`

---

### Task 1: Бизнес-правила — страна и граница возраста

**Files:**
- Modify: `server.py:19-27` (константы), `server.py` (`make_decision`, строки ~92-105 в текущей версии)
- Modify: `tests/test_decision.py`
- Modify: `tests/test_http.py:52-59` (`_adult_payload`)

- [ ] **Step 0: Установить dev-зависимости в venv**

Run: `.venv/bin/pip install -r requirements-dev.txt`
Expected: успешная установка `pytest` (на этом шаге `requirements-dev.txt`
содержит только `pytest>=8,<9`).

- [ ] **Step 1: Обновить хелперы и параметризованные тесты в `tests/test_decision.py`**

Импорт (было `from server import MIN_AGE, calculate_age, make_decision, validate_payload`):

```python
from server import MAX_AGE, MIN_AGE, calculate_age, make_decision, validate_payload
```

`_valid_payload` — добавить `country`:

```python
def _valid_payload(**overrides):
    base = {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        "phone": "+79991234567",
        "birth_date": "1990-05-15",
        "country": "Россия",
        "amount": 100000,
    }
    base.update(overrides)
    return base
```

Оба параметризованных теста — добавить `"country"` в список полей:

```python
@pytest.mark.parametrize("field", ["last_name", "first_name", "phone", "country"])
def test_validate_required_string_missing(field):
    payload = _valid_payload()
    del payload[field]
    _, errors = validate_payload(payload)
    assert any(e["field"] == field for e in errors)


@pytest.mark.parametrize("field", ["last_name", "first_name", "phone", "country"])
def test_validate_required_string_blank(field):
    _, errors = validate_payload(_valid_payload(**{field: "   "}))
    assert any(e["field"] == field for e in errors)
```

`_cleaned` — добавить параметр `country`:

```python
def _cleaned(birth_date, country="Россия"):
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        "phone": "+79991234567",
        "country": country,
        "birth_date": birth_date,
    }
```

- [ ] **Step 2: Добавить новые тесты в конец `tests/test_decision.py`**

```python
# --- MAX_AGE и страна -------------------------------------------------------

def test_decision_max_age_boundary_approved():
    born = date.today().replace(year=date.today().year - MAX_AGE)
    result = make_decision(_cleaned(born))
    assert result["status"] == "approved"
    assert result["reasons"] == []
    assert result["applicant"]["age"] == MAX_AGE


def test_decision_over_max_age_declined():
    born = date.today().replace(year=date.today().year - (MAX_AGE + 1))
    result = make_decision(_cleaned(born))
    assert result["status"] == "declined"
    assert any(str(MAX_AGE) in reason for reason in result["reasons"])


def test_decision_blocked_country_declined():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_cleaned(born, country="Китай"))
    assert result["status"] == "declined"
    assert any("Китай" in reason for reason in result["reasons"])


def test_decision_blocked_country_case_insensitive():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_cleaned(born, country="китай"))
    assert result["status"] == "declined"
```

- [ ] **Step 3: Добавить `country` в `_adult_payload` в `tests/test_http.py`**

Было:

```python
def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "birth_date": born.isoformat(),
    }
```

Стало:

```python
def _adult_payload():
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "phone": "+79991234567",
        "country": "Россия",
        "birth_date": born.isoformat(),
    }
```

- [ ] **Step 4: Запустить тесты — зафиксировать ожидаемые провалы**

Run: `.venv/bin/pytest -q`
Expected: 4 FAILED, остальные PASSED:
- `test_validate_required_string_missing[country]` — FAIL (поле `country` пока не обязательно)
- `test_validate_required_string_blank[country]` — FAIL
- `test_decision_blocked_country_declined` — FAIL (страна пока не проверяется)
- `test_decision_blocked_country_case_insensitive` — FAIL

(`test_decision_max_age_boundary_approved` и `test_decision_over_max_age_declined`
уже проходят — проверка `MAX_AGE = 35` была добавлена ранним коммитом.)

- [ ] **Step 5: Реализовать правила в `server.py`**

Заменить блок констант (`server.py:19-27`):

Было:

```python
# --- Бизнес-правила --------------------------------------------------------

# Единственное правило на текущем этапе: заявителю должно быть не меньше 18 лет.
# Хотите "строго больше 18" — поменяйте сравнение в make_decision на age <= MIN_AGE.
MIN_AGE = 18
MAX_AGE = 35

# Обязательные строковые поля заявки (персональные данные).
REQUIRED_STRING_FIELDS = ("last_name", "first_name", "phone")
```

Стало:

```python
# --- Бизнес-правила --------------------------------------------------------

# Заявителю должно быть от MIN_AGE до MAX_AGE лет включительно.
MIN_AGE = 18
MAX_AGE = 35

# Страны, заявки из которых не принимаются (сравнение без учёта регистра).
BLOCKED_COUNTRIES = {"китай"}

# Обязательные строковые поля заявки.
REQUIRED_STRING_FIELDS = ("last_name", "first_name", "phone", "country")
```

В `make_decision` — добавить проверку страны и убрать пустые строки перед `return`:

Было:

```python
    reasons = []
    if age < MIN_AGE:
        reasons.append(f"Возраст заявителя {age} лет — меньше минимально допустимого {MIN_AGE}")
    if age > MAX_AGE:
        reasons.append(f"Возраст заявителя {age} лет — больше макс допустимого {MAX_AGE}")

    status = "approved" if not reasons else "declined"
    full_name = " ".join(
        part for part in (cleaned["last_name"], cleaned["first_name"], cleaned.get("middle_name")) if part
    )




    return {
```

Стало:

```python
    reasons = []
    if age < MIN_AGE:
        reasons.append(f"Возраст заявителя {age} лет — меньше минимально допустимого {MIN_AGE}")
    if age > MAX_AGE:
        reasons.append(f"Возраст заявителя {age} лет — больше макс допустимого {MAX_AGE}")
    if cleaned["country"].lower() in BLOCKED_COUNTRIES:
        reasons.append(f"Заявки из страны «{cleaned['country']}» не принимаются")

    status = "approved" if not reasons else "declined"
    full_name = " ".join(
        part for part in (cleaned["last_name"], cleaned["first_name"], cleaned.get("middle_name")) if part
    )

    return {
```

- [ ] **Step 6: Запустить тесты — все зелёные**

Run: `.venv/bin/pytest -q`
Expected: все тесты PASSED (включая 4 из Step 4 и существующие).

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_decision.py tests/test_http.py
git commit -m "feat: add country blocklist rule and MAX_AGE boundary tests"
```

---

### Task 2: Зависимости FastAPI/Uvicorn

**Files:**
- Create: `requirements.txt`
- Modify: `requirements-dev.txt`

- [ ] **Step 1: Создать `requirements.txt`**

```
fastapi>=0.110,<1
uvicorn>=0.29,<1
```

- [ ] **Step 2: Обновить `requirements-dev.txt`**

Было:

```
# Зависимости только для тестов/CI. На проде не нужны — сервис работает на голой stdlib.
pytest>=8,<9
```

Стало:

```
-r requirements.txt

# Зависимости для тестов/CI.
pytest>=8,<9
httpx>=0.27,<1
```

(`httpx` нужен для `fastapi.testclient.TestClient` в Task 3.)

- [ ] **Step 3: Установить зависимости в venv**

Run: `.venv/bin/pip install -r requirements-dev.txt`
Expected: успешная установка `fastapi`, `uvicorn`, `httpx` (и их транзитивных
зависимостей) в дополнение к уже установленному `pytest`.

- [ ] **Step 4: Проверить, что пакеты импортируются**

Run: `.venv/bin/python -c "import fastapi, uvicorn, httpx; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Запустить текущие тесты — убедиться, что ничего не сломалось**

Run: `.venv/bin/pytest -q`
Expected: все тесты PASSED (server.py пока не использует новые зависимости).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-dev.txt
git commit -m "build: add fastapi/uvicorn runtime deps and httpx for tests"
```

---

### Task 3: Миграция HTTP-слоя на FastAPI

**Files:**
- Modify: `tests/test_http.py` (полная замена содержимого)
- Modify: `server.py` (полная замена содержимого)

- [ ] **Step 1: Переписать `tests/test_http.py` на `TestClient`**

Полностью заменить содержимое файла на:

```python
"""Интеграционные тесты HTTP-слоя.

fastapi.testclient.TestClient гоняет ASGI-приложение in-process через тот же
стек, что обработал бы настоящий HTTP-запрос — без поднятия реального сокета.
"""

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


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_help(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "PetBank"


def test_application_approved(client):
    resp = client.post("/applications", json=_adult_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["applicant"]["age"] == 30
    assert body["reasons"] == []


def test_application_declined_minor(client):
    payload = _adult_payload()
    payload["birth_date"] = date.today().replace(year=date.today().year - 10).isoformat()
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert body["reasons"]


def test_application_declined_blocked_country(client):
    payload = _adult_payload()
    payload["country"] = "Китай"
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Китай" in reason for reason in body["reasons"])


def test_application_validation_error(client):
    resp = client.post("/applications", json={"first_name": "Иван"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "validation_error"
    assert body["details"]


def test_application_invalid_json(client):
    resp = client.post(
        "/applications",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"


def test_unknown_path_404(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: Запустить тесты — зафиксировать ожидаемую ошибку**

Run: `.venv/bin/pytest -q`
Expected: ошибка сбора тестов в `tests/test_http.py`:
```
ERROR tests/test_http.py - ImportError: cannot import name 'app' from 'server'
```
(в `server.py` пока нет объекта `app` — он есть только в FastAPI-версии).

- [ ] **Step 3: Переписать `server.py` на FastAPI**

Полностью заменить содержимое файла на:

```python
"""PetBank — простейший сервер приёма заявок.

Запуск:  python server.py   (или  python server.py 8080  — другой порт)

Эндпоинты:
    POST /applications  — подать заявку, вернёт решение approved / declined
    GET  /health        — проверка, что сервер жив
    GET  /              — короткая справка
"""

import json
import os
import sys
import uuid
from datetime import date, datetime

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# --- Бизнес-правила --------------------------------------------------------

# Заявителю должно быть от MIN_AGE до MAX_AGE лет включительно.
MIN_AGE = 18
MAX_AGE = 35

# Страны, заявки из которых не принимаются (сравнение без учёта регистра).
BLOCKED_COUNTRIES = {"китай"}

# Обязательные строковые поля заявки.
REQUIRED_STRING_FIELDS = ("last_name", "first_name", "phone", "country")


def calculate_age(birth_date: date, today: date) -> int:
    """Полное число лет на дату `today`."""
    years = today.year - birth_date.year
    # День рождения в этом году ещё не наступил — вычитаем год.
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def validate_payload(payload):
    """Проверяет тело заявки. Возвращает (cleaned, errors).

    cleaned — нормализованные данные, errors — список ошибок (пустой, если всё ок).
    """
    if not isinstance(payload, dict):
        return None, [{"field": "<body>", "message": "Тело запроса должно быть JSON-объектом"}]

    errors = []
    cleaned = {}

    for field in REQUIRED_STRING_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": field, "message": "Обязательное поле: непустая строка"})
        else:
            cleaned[field] = value.strip()

    middle = payload.get("middle_name")
    if middle is not None and not isinstance(middle, str):
        errors.append({"field": "middle_name", "message": "Должно быть строкой"})
    else:
        cleaned["middle_name"] = (middle or "").strip()

    birth_raw = payload.get("birth_date")
    if not isinstance(birth_raw, str) or not birth_raw.strip():
        errors.append({"field": "birth_date", "message": "Обязательное поле в формате YYYY-MM-DD"})
    else:
        try:
            birth = datetime.strptime(birth_raw.strip(), "%Y-%m-%d").date()
            if birth > date.today():
                errors.append({"field": "birth_date", "message": "Дата рождения не может быть в будущем"})
            else:
                cleaned["birth_date"] = birth
        except ValueError:
            errors.append({"field": "birth_date", "message": "Неверный формат даты, ожидается YYYY-MM-DD"})

    amount = payload.get("amount")
    if amount is not None:
        # bool — подкласс int, поэтому исключаем его явно.
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            errors.append({"field": "amount", "message": "Должно быть неотрицательным числом"})
        else:
            cleaned["amount"] = amount

    return cleaned, errors


def make_decision(cleaned):
    """Принимает решение по уже провалидированной заявке."""
    today = date.today()
    age = calculate_age(cleaned["birth_date"], today)

    reasons = []
    if age < MIN_AGE:
        reasons.append(f"Возраст заявителя {age} лет — меньше минимально допустимого {MIN_AGE}")
    if age > MAX_AGE:
        reasons.append(f"Возраст заявителя {age} лет — больше макс допустимого {MAX_AGE}")
    if cleaned["country"].lower() in BLOCKED_COUNTRIES:
        reasons.append(f"Заявки из страны «{cleaned['country']}» не принимаются")

    status = "approved" if not reasons else "declined"
    full_name = " ".join(
        part for part in (cleaned["last_name"], cleaned["first_name"], cleaned.get("middle_name")) if part
    )

    return {
        "application_id": str(uuid.uuid4()),
        "status": status,
        "applicant": {
            "full_name": full_name,
            "age": age,
            "phone": cleaned["phone"],
        },
        "reasons": reasons,
        "received_at": datetime.now().isoformat(timespec="seconds"),
    }


# --- HTTP-слой ---------------------------------------------------------------

app = FastAPI(title="PetBank")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "service": "PetBank",
        "endpoints": ["POST /applications", "GET /health"],
        "rule": f"возраст {MIN_AGE}-{MAX_AGE}, страна не в стоп-листе",
    }


@app.post("/applications")
async def create_application(request: Request):
    raw = await request.body()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "bad_request", "message": "Пустое тело запроса"})

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "message": "Тело запроса не является валидным JSON"},
        )

    cleaned, errors = validate_payload(payload)
    if errors:
        return JSONResponse(status_code=400, content={
            "error": "validation_error",
            "message": "Проверьте поля заявки",
            "details": errors,
        })

    return make_decision(cleaned)


def run(host="0.0.0.0", port=None):
    port = port or int(os.environ.get("PORT", "8000"))
    print(f"PetBank запущен: http://localhost:{port}  (Ctrl+C — остановить)")
    print(
        f"Правило одобрения: возраст {MIN_AGE}-{MAX_AGE} лет, "
        f"страна не в стоп-листе ({', '.join(sorted(BLOCKED_COUNTRIES))})"
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(port=cli_port)
```

- [ ] **Step 4: Запустить тесты — все зелёные**

Run: `.venv/bin/pytest -q`
Expected: все тесты PASSED (16 тестов в `test_http.py` + тесты `test_decision.py`).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_http.py
git commit -m "refactor: migrate HTTP layer from http.server to FastAPI+Uvicorn"
```

---

### Task 4: Обновление деплоя (systemd + инструкция)

**Files:**
- Modify: `deploy/petbank.service`
- Modify: `deploy/README.md`

- [ ] **Step 1: Обновить `ExecStart` в `deploy/petbank.service`**

Было:

```ini
ExecStart=/usr/bin/python3 /opt/petbank/server.py
```

Стало:

```ini
ExecStart=/opt/petbank/.venv/bin/python /opt/petbank/server.py
```

- [ ] **Step 2: Добавить предупреждение про мёрж в `deploy/README.md`**

Вставить новый раздел сразу после первого абзаца (после описания CI/CD,
перед `## Что нужно в GitHub Secrets`):

```markdown
## ⚠️ Перед мёржем в main (переход на FastAPI)

Сервис теперь требует `fastapi`/`uvicorn` (см. `requirements.txt`). CI
деплоит автоматически при мёрже в `main` и сразу перезапускает сервис —
если на VM ещё нет venv с этими зависимостями, `systemctl restart petbank`
упадёт с `ModuleNotFoundError`, health-check в CI станет красным, прод
будет недоступен.

**До мёржа вручную на VM:**
1. `python3 -m venv /opt/petbank/.venv && /opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt`
   (нужен файл `/opt/petbank/requirements.txt` — например, разово
   засинхронизировать код этой ветки на VM перед мёржем).
2. Обновить `/etc/systemd/system/petbank.service` (новый `ExecStart`, см.
   ниже) и выполнить `systemctl daemon-reload`.
```

- [ ] **Step 3: Обновить описание стека в `deploy/README.md`**

Было:

```markdown
Деплой = `rsync` кода в `/opt/petbank` на сервере + `systemctl restart petbank`.
Сервис — обычный systemd-юнит, работает под непривилегированным юзером `deploy`,
слушает `:8000`. Никакого Docker и pip на проде: приложение на голой stdlib.
```

Стало:

```markdown
Деплой = `rsync` кода в `/opt/petbank` на сервере + `systemctl restart petbank`.
Сервис — обычный systemd-юнит, работает под непривилегированным юзером `deploy`,
слушает `:8000`. Никакого Docker на проде: зависимости (FastAPI/Uvicorn)
ставятся в venv `/opt/petbank/.venv`, системный Python не трогаем.
```

- [ ] **Step 4: Добавить `python3-venv` в установку пакетов**

Было:

```bash
# rsync для деплоя + фаервол
apt-get update && apt-get install -y rsync
ufw allow OpenSSH
ufw allow 8000/tcp
ufw --force enable
```

Стало:

```bash
# rsync для деплоя, venv-модуль + фаервол
apt-get update && apt-get install -y rsync python3-venv
ufw allow OpenSSH
ufw allow 8000/tcp
ufw --force enable
```

- [ ] **Step 5: Добавить шаг создания venv после первого rsync**

Было:

```markdown
После первого `rsync` кода в `/opt/petbank` поднять сервис:
`systemctl start petbank` и проверить `curl http://127.0.0.1:8000/health`.
```

Стало:

```markdown
После первого `rsync` кода в `/opt/petbank` — создать venv и поставить
зависимости (повторять при каждом обновлении `requirements.txt`):

```bash
python3 -m venv /opt/petbank/.venv
/opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt
```

Затем поднять сервис: `systemctl start petbank` и проверить
`curl http://127.0.0.1:8000/health`.
```

- [ ] **Step 6: Commit**

```bash
git add deploy/petbank.service deploy/README.md
git commit -m "docs(deploy): switch systemd unit to venv and document FastAPI rollout"
```

---

### Task 5: Документация (README, OpenAPI, requests.http)

**Files:**
- Modify: `README.md`
- Modify: `openapi.yaml`
- Modify: `requests.http`

- [ ] **Step 1: Обновить шапку и стек в `README.md`**

Было:

```markdown
# PetBank

Учебный «банк». Сервер принимает заявку с персональными данными (ФИО, телефон, дата
рождения) и возвращает решение: **approved** или **declined**.

Текущее правило одобрения одно: **заявителю должно быть не меньше 18 лет**.

## Стек

Только стандартная библиотека Python (`http.server`) — **никаких зависимостей и `pip install`**.
Нужен только сам Python 3.8+. Скачать: https://www.python.org/downloads/
(при установке поставьте галочку «Add python.exe to PATH»).
```

Стало:

```markdown
# PetBank

Учебный «банк». Сервер принимает заявку с персональными данными (ФИО, телефон, дата
рождения, страна) и возвращает решение: **approved** или **declined**.

Правила одобрения:
- заявителю должно быть от **18 до 35 лет** включительно;
- страна заявителя не должна быть в стоп-листе (по умолчанию — «Китай»).

## Стек

[FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/).
Нужен Python 3.8+ и установленные зависимости:

```bash
pip install -r requirements.txt
```
```

- [ ] **Step 2: Обновить пример запроса/ответа в `README.md`**

Было:

```json
{
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "phone": "+79991234567",
  "birth_date": "1990-05-15",
  "amount": 100000
}
```

Стало:

```json
{
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "phone": "+79991234567",
  "birth_date": "1990-05-15",
  "country": "Россия",
  "amount": 100000
}
```

Было:

```markdown
Если возраст меньше 18 — `status: "declined"` и причина в `reasons`.
```

Стало:

```markdown
Если возраст вне диапазона 18–35 или страна — в стоп-листе, `status: "declined"`,
причины — в `reasons`.
```

- [ ] **Step 3: Обновить curl/PowerShell примеры в `README.md`**

Было:

```bash
curl -X POST http://localhost:8000/applications \
  -H "Content-Type: application/json" \
  -d "{\"last_name\":\"Иванов\",\"first_name\":\"Иван\",\"phone\":\"+79991234567\",\"birth_date\":\"1990-05-15\"}"
```

Стало:

```bash
curl -X POST http://localhost:8000/applications \
  -H "Content-Type: application/json" \
  -d "{\"last_name\":\"Иванов\",\"first_name\":\"Иван\",\"phone\":\"+79991234567\",\"birth_date\":\"1990-05-15\",\"country\":\"Россия\"}"
```

Было:

```powershell
$body = @{ last_name="Иванов"; first_name="Иван"; phone="+79991234567"; birth_date="1990-05-15" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/applications -Method Post -ContentType "application/json" -Body $body
```

Стало:

```powershell
$body = @{ last_name="Иванов"; first_name="Иван"; phone="+79991234567"; birth_date="1990-05-15"; country="Россия" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/applications -Method Post -ContentType "application/json" -Body $body
```

- [ ] **Step 4: Добавить `requirements.txt` в раздел «Файлы» в `README.md`**

Было:

```markdown
## Файлы

- `server.py` — сам сервер (вся логика тут).
- `main.py` — точка входа (запускает `server.py`).
- `openapi.yaml` — контракт API, импортируется в Postman.
- `requests.http` — готовые запросы для PyCharm.
```

Стало:

```markdown
## Файлы

- `server.py` — сам сервер (вся логика тут).
- `main.py` — точка входа (запускает `server.py`).
- `requirements.txt` — зависимости для запуска (FastAPI, Uvicorn).
- `openapi.yaml` — контракт API, импортируется в Postman.
- `requests.http` — готовые запросы для PyCharm.
```

- [ ] **Step 5: Обновить `openapi.yaml`**

Было (шапка):

```yaml
info:
  title: PetBank — сервис заявок
  description: |
    Учебный «банк». Принимает заявку с персональными данными и возвращает решение.
    Текущее правило: заявителю должно быть не меньше 18 лет.
  version: 0.1.0
```

Стало:

```yaml
info:
  title: PetBank — сервис заявок
  description: |
    Учебный «банк». Принимает заявку с персональными данными и возвращает решение.
    Правила: возраст заявителя от 18 до 35 лет включительно, страна — не в стоп-листе
    (по умолчанию запрещён «Китай»).
  version: 0.2.0
```

Было (примеры в `/applications`):

```yaml
            examples:
              approve:
                summary: Совершеннолетний — будет одобрено
                value:
                  last_name: Иванов
                  first_name: Иван
                  middle_name: Иванович
                  phone: "+79991234567"
                  birth_date: "1990-05-15"
                  amount: 100000
              decline:
                summary: Младше 18 — будет отказано
                value:
                  last_name: Петров
                  first_name: Пётр
                  phone: "+79990001122"
                  birth_date: "2010-01-01"
```

Стало:

```yaml
            examples:
              approve:
                summary: Возраст 18-35, страна разрешена — будет одобрено
                value:
                  last_name: Иванов
                  first_name: Иван
                  middle_name: Иванович
                  phone: "+79991234567"
                  birth_date: "1990-05-15"
                  country: Россия
                  amount: 100000
              decline_age:
                summary: Младше 18 — будет отказано
                value:
                  last_name: Петров
                  first_name: Пётр
                  phone: "+79990001122"
                  birth_date: "2010-01-01"
                  country: Россия
              decline_country:
                summary: Страна в стоп-листе — будет отказано
                value:
                  last_name: Ли
                  first_name: Вэй
                  phone: "+861234567890"
                  birth_date: "1990-05-15"
                  country: Китай
```

Было (схема `ApplicationRequest`):

```yaml
    ApplicationRequest:
      type: object
      required: [last_name, first_name, phone, birth_date]
      properties:
        last_name:
          type: string
          description: Фамилия
          example: Иванов
        first_name:
          type: string
          description: Имя
          example: Иван
        middle_name:
          type: string
          description: Отчество (необязательно)
          example: Иванович
        phone:
          type: string
          description: Телефон
          example: "+79991234567"
        birth_date:
          type: string
          format: date
          description: Дата рождения в формате YYYY-MM-DD
          example: "1990-05-15"
        amount:
          type: number
          description: Запрашиваемая сумма (необязательно)
          example: 100000
```

Стало:

```yaml
    ApplicationRequest:
      type: object
      required: [last_name, first_name, phone, birth_date, country]
      properties:
        last_name:
          type: string
          description: Фамилия
          example: Иванов
        first_name:
          type: string
          description: Имя
          example: Иван
        middle_name:
          type: string
          description: Отчество (необязательно)
          example: Иванович
        phone:
          type: string
          description: Телефон
          example: "+79991234567"
        birth_date:
          type: string
          format: date
          description: Дата рождения в формате YYYY-MM-DD
          example: "1990-05-15"
        country:
          type: string
          description: Страна заявителя. Заявки из стран из стоп-листа (по умолчанию "Китай") отклоняются.
          example: Россия
        amount:
          type: number
          description: Запрашиваемая сумма (необязательно)
          example: 100000
```

- [ ] **Step 6: Добавить `country` в `requests.http`**

Было:

```http
### Заявка — должно ОДОБРИТЬ (18+)
POST http://localhost:8000/applications
Content-Type: application/json

{
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "phone": "+79991234567",
  "birth_date": "1990-05-15",
  "amount": 100000
}

### Заявка — должно ОТКАЗАТЬ (младше 18)
POST http://localhost:8000/applications
Content-Type: application/json

{
  "last_name": "Петров",
  "first_name": "Пётр",
  "phone": "+79990001122",
  "birth_date": "2010-01-01"
}
```

Стало:

```http
### Заявка — должно ОДОБРИТЬ (возраст 18-35, страна разрешена)
POST http://localhost:8000/applications
Content-Type: application/json

{
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "phone": "+79991234567",
  "birth_date": "1990-05-15",
  "country": "Россия",
  "amount": 100000
}

### Заявка — должно ОТКАЗАТЬ (младше 18)
POST http://localhost:8000/applications
Content-Type: application/json

{
  "last_name": "Петров",
  "first_name": "Пётр",
  "phone": "+79990001122",
  "birth_date": "2010-01-01",
  "country": "Россия"
}

### Заявка — должно ОТКАЗАТЬ (страна в стоп-листе)
POST http://localhost:8000/applications
Content-Type: application/json

{
  "last_name": "Ли",
  "first_name": "Вэй",
  "phone": "+861234567890",
  "birth_date": "1990-05-15",
  "country": "Китай"
}
```

- [ ] **Step 7: Commit**

```bash
git add README.md openapi.yaml requests.http
git commit -m "docs: document country field, age range and FastAPI stack"
```

---

### Task 6: Финальная проверка

**Files:** нет изменений, только проверка.

- [ ] **Step 1: Полный прогон тестов**

Run: `.venv/bin/pytest -q`
Expected: все тесты PASSED.

- [ ] **Step 2: Ручной smoke-тест сервера**

```bash
.venv/bin/python server.py 8001 &
SERVER_PID=$!
sleep 1
curl -s http://127.0.0.1:8001/health
curl -s http://127.0.0.1:8001/
curl -s -X POST http://127.0.0.1:8001/applications \
  -H "Content-Type: application/json" \
  -d '{"last_name":"Иванов","first_name":"Иван","phone":"+79991234567","birth_date":"1990-05-15","country":"Россия"}'
curl -s -X POST http://127.0.0.1:8001/applications \
  -H "Content-Type: application/json" \
  -d '{"last_name":"Ли","first_name":"Вэй","phone":"+861234567890","birth_date":"1990-05-15","country":"Китай"}'
kill $SERVER_PID
```

Expected:
- `/health` → `{"status": "ok"}`
- `/` → JSON со `"service": "PetBank"` и обновлённым `"rule"`
- первая заявка → `"status": "approved"`, `"reasons": []`
- вторая заявка → `"status": "declined"`, в `"reasons"` упоминается «Китай»

- [ ] **Step 3: Напоминание про деплой**

⚠️ Перед тем как мёржить ветку `add-old-age-condition` в `main`:
1. На VM создать venv и установить `requirements.txt`
   (`python3 -m venv /opt/petbank/.venv && /opt/petbank/.venv/bin/pip install -r /opt/petbank/requirements.txt`).
2. Обновить `/etc/systemd/system/petbank.service` (новый `ExecStart` на venv)
   и выполнить `systemctl daemon-reload`.

Без этих шагов автодеплой при мёрже в `main` уронит прод (см.
`deploy/README.md`, раздел «⚠️ Перед мёржем в main»).
