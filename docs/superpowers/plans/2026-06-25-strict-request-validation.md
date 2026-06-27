# Строгая валидация входных данных заявки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать строгость поля `birth_date` (формат `ГГГГ-ММ-ДД`) явной собственным валидатором с русскими сообщениями и закрепить тестами модели и HTTP-ручки, не меняя контракт API.

**Architecture:** В `ApplicationRequest` (`server.py`) добавляется `field_validator("birth_date", mode="before")`, который принимает строку строго `ГГГГ-ММ-ДД` (regex + `strptime`) и готовый объект `date`, а всё прочее (число, `datetime`, иные форматы) отклоняет с понятной русской ошибкой. Существующая проверка «не в будущем» (`mode="after"`) и валидатор строковых полей сохраняются. Поведение валидных запросов не меняется.

**Tech Stack:** Python 3, Pydantic v2, FastAPI, pytest (через `.venv` основного дерева).

## Global Constraints

- Интерпретатор для всех команд (запуск из корня worktree):
  `PY="/Users/ekaterina/Desktop/github/mybestpetfinancialproject/.venv/bin/python"`
- Все сообщения об ошибках валидации — на русском.
- Контракт API НЕ расширяем: без новых полей, без `extra="forbid"`, без Enum-списка стран, без валидации формата телефона.
- Бизнес-логику (`make_decision`, возрастные правила, стоп-лист стран) не трогаем.
- Поле `country` и валидатор `strip_and_require_nonempty` не меняем (страна уже обязательная непустая строка); по стране только добавляем тест.
- Git: ветка `feat/strict-request-validation` уже создана от свежего `origin/main`; merge-коммиты запрещены (только rebase); сообщения коммитов на русском.
- База перед началом: `pytest` = 55 passed. После задач ожидается 67 passed (+10 в модели, +2 в HTTP).

---

### Task 1: Строгий валидатор `birth_date` + юнит-тесты модели

**Files:**
- Modify: `server.py` (импорт `re`; новый валидатор в `ApplicationRequest` перед `birth_date_not_future`, ~стр. 81)
- Test: `tests/test_decision.py` (добавить тесты после `test_request_birth_date_in_future`, ~стр. 116)

**Interfaces:**
- Produces: `ApplicationRequest.parse_strict_birth_date(cls, v: object) -> date` — Pydantic `field_validator("birth_date", mode="before")`. Принимает `str` строго `ГГГГ-ММ-ДД` и `date` (не `datetime`); иначе бросает `ValueError` с русским текстом.
- Consumes: существующий хелпер `_valid_request(**overrides)` из `tests/test_decision.py`.

- [ ] **Step 1: Написать падающий тест на русский текст ошибки**

В `tests/test_decision.py` добавить:

```python
def test_request_birth_date_error_message_russian():
    with pytest.raises(Exception) as exc:
        _valid_request(birth_date="15.05.2000")
    assert "ГГГГ-ММ-ДД" in str(exc.value)
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd <worktree> && "$PY" -m pytest tests/test_decision.py::test_request_birth_date_error_message_russian -q`
Expected: FAIL — на текущем коде сообщение Pydantic англоязычное («Input should be a valid date…»), подстроки `ГГГГ-ММ-ДД` в нём нет.

- [ ] **Step 3: Реализовать валидатор в `server.py`**

В блоке импортов добавить `import re` (между `import os` и `import sys`):

```python
import logging
import os
import re
import sys
import uuid
from datetime import date, datetime
```

В классе `ApplicationRequest` непосредственно перед методом `birth_date_not_future` вставить:

```python
    @field_validator("birth_date", mode="before")
    @classmethod
    def parse_strict_birth_date(cls, v: object) -> date:
        # Уже чистый date (но не datetime) — принимаем как есть.
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if not isinstance(v, str):
            raise ValueError("Дата должна быть строкой в формате ГГГГ-ММ-ДД")
        s = v.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            raise ValueError(
                "Дата должна быть в формате ГГГГ-ММ-ДД (например, 2000-05-15)"
            )
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Несуществующая дата (проверьте месяц и день)")
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `cd <worktree> && "$PY" -m pytest tests/test_decision.py::test_request_birth_date_error_message_russian -q`
Expected: PASS.

- [ ] **Step 5: Добавить закрепляющие тесты модели**

В `tests/test_decision.py` добавить (рядом с тестом из Step 1):

```python
@pytest.mark.parametrize("bad_value", [
    "15.05.2000",
    "2000/05/15",
    "2000-5-5",
    "2000-05-15T10:00:00",
    1000000,
    20000515,
])
def test_request_birth_date_strict_format_rejected(bad_value):
    with pytest.raises(Exception):
        _valid_request(birth_date=bad_value)


def test_request_birth_date_nonexistent_rejected():
    with pytest.raises(Exception):
        _valid_request(birth_date="2000-13-40")


def test_request_birth_date_accepts_date_object():
    req = _valid_request(birth_date=date(2000, 5, 15))
    assert req.birth_date == date(2000, 5, 15)


def test_request_country_not_string_rejected():
    with pytest.raises(Exception):
        _valid_request(country=123)
```

Примечание: эти тесты характеризуют поведение — `parse_strict_birth_date` и `strip_and_require_nonempty` обеспечивают их прохождение; красная фаза была покрыта тестом из Step 1.

- [ ] **Step 6: Прогнать весь файл тестов модели**

Run: `cd <worktree> && "$PY" -m pytest tests/test_decision.py -q`
Expected: PASS, новых провалов нет (существующие тесты, включая `_valid_decision_request` с объектом `date`, проходят).

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_decision.py
git commit -m "feat: строгий валидатор даты рождения (ГГГГ-ММ-ДД) с русскими ошибками

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Тесты HTTP-ручки `POST /applications`

**Files:**
- Test: `tests/test_http.py` (добавить после `test_application_validation_error`, ~стр. 79)

**Interfaces:**
- Consumes: фикстура `client` (`TestClient(app)`) и хелпер `_adult_payload()` из `tests/test_http.py`; валидатор `parse_strict_birth_date` из Task 1.

- [ ] **Step 1: Написать тесты ручки**

В `tests/test_http.py` добавить:

```python
def test_application_invalid_birth_date_returns_422(client):
    payload = _adult_payload()
    payload["birth_date"] = "15.05.2000"
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    fields_with_errors = [e["loc"][-1] for e in body["detail"]]
    assert "birth_date" in fields_with_errors


def test_application_valid_birth_date_ok(client):
    payload = _adult_payload()
    payload["birth_date"] = "2000-05-15"
    resp = client.post("/applications", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "applicant" in body
```

- [ ] **Step 2: Прогнать тесты ручки**

Run: `cd <worktree> && "$PY" -m pytest tests/test_http.py -q`
Expected: PASS. Тесты характеризуют контракт ручки: невалидная дата → `422` с ошибкой по `birth_date`; валидная дата → `200` с решением. Защищают от регрессий валидации на HTTP-слое.

- [ ] **Step 3: Commit**

```bash
git add tests/test_http.py
git commit -m "test: проверки ручки /applications на валидную и невалидную дату

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Финальная проверка и PR

**Files:** нет изменений кода — проверка и доставка.

- [ ] **Step 1: Прогнать весь набор тестов**

Run: `cd <worktree> && "$PY" -m pytest tests/ -q`
Expected: PASS, 67 passed (было 55, добавлено 10 в модели + 2 в HTTP).

- [ ] **Step 2: Проверить, что ветка чистая и от свежего main**

Run: `git status --short && git log --oneline origin/main..HEAD`
Expected: рабочее дерево чистое; в логе — коммит спека, коммит Task 1, коммит Task 2 (плюс, при наличии, коммит плана).

- [ ] **Step 3: (если нужно) rebase на свежий origin/main**

Run: `git fetch origin && git rebase origin/main`
Expected: без конфликтов (изменения локальны для `server.py` и тестов).

- [ ] **Step 4: Push ветки**

Run: `git push -u origin feat/strict-request-validation`

- [ ] **Step 5: Создать PR**

Если доступен `gh`: `gh pr create --base main --head feat/strict-request-validation --title "feat: строгая валидация даты рождения и тесты" --body "<краткое описание из спека>"`.
Если `gh` недоступен: вывести ссылку вида `https://github.com/<owner>/mybestpetfinancialproject/compare/main...feat/strict-request-validation?expand=1` и не мёржить (мёрж — отдельным шагом по решению пользователя; merge-коммиты запрещены, мёрж только rebase).

---

## Self-Review

**Spec coverage:**
- Явный строгий валидатор даты `ГГГГ-ММ-ДД` → Task 1, Step 3. ✔
- Русские сообщения об ошибках → Task 1, Steps 1–4. ✔
- Совместимость с объектом `date` → Task 1, Step 5 (`test_request_birth_date_accepts_date_object`). ✔
- Страна — обязательная строка, тест на не-строку → Task 1, Step 5 (`test_request_country_not_string_rejected`). ✔
- Тесты модели (плохие форматы, несуществующая дата) → Task 1, Step 5. ✔
- HTTP-тест невалидного запроса → ошибка ручки → Task 2 (`test_application_invalid_birth_date_returns_422`). ✔
- HTTP-тест валидной даты → ошибки нет → Task 2 (`test_application_valid_birth_date_ok`). ✔
- Git/PR от origin/main, rebase, PR → Task 3. ✔

**Placeholder scan:** плейсхолдеров нет; код приведён полностью в каждом шаге.

**Type consistency:** `parse_strict_birth_date(cls, v: object) -> date` определён в Task 1 и используется (через ручку) в Task 2; имена хелперов `_valid_request`, `_adult_payload`, фикстура `client` соответствуют существующим в тестах.
