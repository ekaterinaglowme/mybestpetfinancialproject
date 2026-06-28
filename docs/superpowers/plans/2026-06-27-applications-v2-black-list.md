# План реализации: `POST /applications/v2` с проверкой паспорта по чёрному списку

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить ручку `POST /applications/v2`, которая перед выдачей кредита проверяет паспорт по внешнему чёрному списку; v1 `/applications` остаётся без изменений.

**Architecture:** Новый async-клиент `black_list.py` (httpx, свой таймаут, fail-closed). Модели запроса рефакторятся в общий `ApplicationBase` + `ApplicationRequest` (v1) и `ApplicationRequestV2` (новые поля, без country). Отдельная функция решения `make_decision_v2` (правила: возраст < 18 и чёрный список). Новые поля заявки сохраняются в `applications` (миграция Alembic 0002).

**Tech Stack:** FastAPI, Pydantic v2, httpx, SQLAlchemy async, Alembic, pytest (+pytest-asyncio).

## Global Constraints

- Код приложения лежит в `app/src/`; импорты плоские (`pythonpath = ["app/src"]`, `from server import ...`).
- Тесты в `tests/`, запуск `pytest` из корня; БД в тестах — файловый SQLite через autouse-фикстуру `conftest.py` (Alembic в тестах НЕ применяется, схема создаётся `Base.metadata.create_all`).
- Коммиты — conventional, по-русски. Мёрж-коммиты запрещены (rebase-only).
- v1 `POST /applications` менять нельзя — его тесты должны остаться зелёными.
- Новая зависимость: `httpx>=0.27`.
- Поведение при недоступности чёрного списка — **fail-closed**: `status="declined"` (HTTP 200).
- Терминология в коде — `black_list` (а не stoplist/terror).
- Дефолты env: `BLACK_LIST_URL="http://212.147.238.3:8090"`, `BLACK_LIST_TIMEOUT_SECONDS="0.8"`.

---

## Структура файлов

- Create: `app/src/black_list.py` — клиент чёрного списка.
- Create: `alembic/versions/0002_applications_v2.py` — миграция.
- Create: `tests/test_models_v2.py`, `tests/test_black_list.py`, `tests/test_decision_v2.py`, `tests/test_http_v2.py`.
- Modify: `app/src/server.py` — рефактор моделей, `make_decision_v2`, ручка v2, расширение мидлваров.
- Modify: `app/src/models.py` — колонки в `Application`, `country` nullable.
- Modify: `app/src/repository.py` — новые параметры `save_application`.
- Modify: `app/src/request_timeout.py`, `app/src/ratelimit.py` — матч по набору путей.
- Modify: `requirements.txt` — добавить `httpx`.
- Modify: `README.md` — раздел про env чёрного списка.

---

## Task 1: Рефактор моделей запроса + `ApplicationRequestV2`

**Files:**
- Modify: `app/src/server.py:56-134` (блок Pydantic-моделей)
- Test: `tests/test_models_v2.py`

**Interfaces:**
- Produces:
  - `ApplicationBase(BaseModel)` — общие поля `last_name, first_name, middle_name, phone, birth_date, amount` + валидаторы.
  - `ApplicationRequest(ApplicationBase)` — v1, добавляет `country: str` (поведение прежнее).
  - `ApplicationRequestV2(ApplicationBase)` — поля `email: str`, `passport: str`, `region: str`, `loan_purpose: Literal["покупка", "перекредитование"]`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_models_v2.py`:

```python
import pytest
from pydantic import ValidationError

from server import ApplicationRequestV2

VALID = dict(
    last_name="Иванов", first_name="Иван", phone="+79991234567",
    birth_date="2000-05-15", amount=100000,
    email="ivan@example.ru", passport="1234567890",
    region="Москва", loan_purpose="покупка",
)


def test_v2_valid():
    m = ApplicationRequestV2(**VALID)
    assert m.passport == "1234567890"
    assert m.region == "Москва"
    assert m.loan_purpose == "покупка"
    assert m.email == "ivan@example.ru"


def test_v2_missing_passport():
    data = {k: v for k, v in VALID.items() if k != "passport"}
    with pytest.raises(ValidationError):
        ApplicationRequestV2(**data)


def test_v2_bad_email():
    with pytest.raises(ValidationError):
        ApplicationRequestV2(**{**VALID, "email": "not-an-email"})


def test_v2_bad_loan_purpose():
    with pytest.raises(ValidationError):
        ApplicationRequestV2(**{**VALID, "loan_purpose": "рефинанс"})


def test_v2_empty_region():
    with pytest.raises(ValidationError):
        ApplicationRequestV2(**{**VALID, "region": "   "})


def test_v2_strips_passport():
    m = ApplicationRequestV2(**{**VALID, "passport": "  1234567890  "})
    assert m.passport == "1234567890"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `pytest tests/test_models_v2.py -v`
Expected: FAIL — `ImportError: cannot import name 'ApplicationRequestV2' from 'server'`.

- [ ] **Step 3: Реализовать рефактор моделей**

В `app/src/server.py` добавить импорт `Literal` к существующим импортам:

```python
from typing import Literal
```

Заменить блок классов `ApplicationRequest` (строки ~58-134) на следующий. Над классами добавить модуль-уровневые помощники:

```python
# Общая проверка «непустая строка после strip».
def _strip_required_nonempty(v: object) -> str:
    if not isinstance(v, str):
        raise ValueError("Обязательное поле: непустая строка")
    stripped = v.strip()
    if not stripped:
        raise ValueError("Обязательное поле: непустая строка")
    return stripped


_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class ApplicationBase(BaseModel):
    last_name: str
    first_name: str
    middle_name: str = ""
    phone: str
    birth_date: date
    amount: float | None = None

    @field_validator("last_name", "first_name", "phone", mode="before")
    @classmethod
    def _v_required_nonempty(cls, v: object) -> str:
        return _strip_required_nonempty(v)

    @field_validator("middle_name", mode="before")
    @classmethod
    def strip_middle_name(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("Должно быть строкой")
        return v.strip()

    @field_validator("birth_date", mode="before")
    @classmethod
    def parse_strict_birth_date(cls, v: object) -> date:
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


class ApplicationRequest(ApplicationBase):
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

    country: str

    @field_validator("country", mode="before")
    @classmethod
    def _v_country(cls, v: object) -> str:
        return _strip_required_nonempty(v)


class ApplicationRequestV2(ApplicationBase):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "last_name": "Иванов",
            "first_name": "Иван",
            "middle_name": "Иванович",
            "phone": "+79991234567",
            "birth_date": "2000-05-15",
            "email": "ivan@example.ru",
            "passport": "1234567890",
            "region": "Москва",
            "loan_purpose": "покупка",
            "amount": 100000,
        }
    })

    email: str
    passport: str
    region: str
    loan_purpose: Literal["покупка", "перекредитование"]

    @field_validator("passport", "region", mode="before")
    @classmethod
    def _v_required_nonempty_v2(cls, v: object) -> str:
        return _strip_required_nonempty(v)

    @field_validator("email", mode="before")
    @classmethod
    def _v_email(cls, v: object) -> str:
        s = _strip_required_nonempty(v)
        if not _EMAIL_RE.fullmatch(s):
            raise ValueError("Некорректный email")
        return s
```

- [ ] **Step 4: Запустить новый тест — должен пройти**

Run: `pytest tests/test_models_v2.py -v`
Expected: PASS (6 тестов).

- [ ] **Step 5: Регрессия v1 — весь набор тестов зелёный**

Run: `pytest -q`
Expected: PASS (включая `tests/test_models.py`, `tests/test_http.py` — поведение v1 не изменилось).

- [ ] **Step 6: Коммит**

```bash
git add app/src/server.py tests/test_models_v2.py
git commit -m "refactor: общий ApplicationBase и модель ApplicationRequestV2"
```

---

## Task 2: Клиент чёрного списка `black_list.py`

**Files:**
- Create: `app/src/black_list.py`
- Modify: `requirements.txt`
- Modify: `README.md`
- Test: `tests/test_black_list.py`

**Interfaces:**
- Produces:
  - `async def check_passport(passport: str) -> bool` — `True`, если паспорт в чёрном списке; бросает `BlackListError` при любом сбое.
  - `class BlackListError(Exception)`.
  - `def _make_client() -> httpx.AsyncClient` — фабрика клиента (точка подмены в тестах).

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_black_list.py`:

```python
import httpx
import pytest

import black_list


def _patch_client(monkeypatch, handler):
    def factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://bl.test",
        )
    monkeypatch.setattr(black_list, "_make_client", factory)


async def test_passport_in_list(monkeypatch):
    def handler(request):
        assert request.url.path == "/check"
        assert request.url.params["passport"] == "111"
        return httpx.Response(200, json={"passport": "111", "in_terror_list": True})
    _patch_client(monkeypatch, handler)
    assert await black_list.check_passport("111") is True


async def test_passport_not_in_list(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"passport": "222", "in_terror_list": False})
    _patch_client(monkeypatch, handler)
    assert await black_list.check_passport("222") is False


async def test_server_error_raises(monkeypatch):
    def handler(request):
        return httpx.Response(500)
    _patch_client(monkeypatch, handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("333")


async def test_malformed_json_raises(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"passport": "444"})  # нет in_terror_list
    _patch_client(monkeypatch, handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("444")


async def test_timeout_raises(monkeypatch):
    def handler(request):
        raise httpx.TimeoutException("timed out")
    _patch_client(monkeypatch, handler)
    with pytest.raises(black_list.BlackListError):
        await black_list.check_passport("555")
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `pytest tests/test_black_list.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'black_list'`.

- [ ] **Step 3: Реализовать клиент**

Создать `app/src/black_list.py`:

```python
"""Клиент сервиса чёрного списка паспортов.

Внешний сервис: GET {BLACK_LIST_URL}/check?passport=... -> {"in_terror_list": bool}.
Любой сбой связи/ответа -> BlackListError; вызывающий применяет fail-closed
(отклоняет заявку).
"""

import os

import httpx

BLACK_LIST_URL = os.environ.get("BLACK_LIST_URL", "http://212.147.238.3:8090")
BLACK_LIST_TIMEOUT_SECONDS = float(os.environ.get("BLACK_LIST_TIMEOUT_SECONDS", "0.8"))


class BlackListError(Exception):
    """Не удалось получить корректный ответ от сервиса чёрного списка."""


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BLACK_LIST_URL, timeout=BLACK_LIST_TIMEOUT_SECONDS,
    )


async def check_passport(passport: str) -> bool:
    """True — паспорт в чёрном списке. Бросает BlackListError при любом сбое."""
    try:
        async with _make_client() as client:
            resp = await client.get("/check", params={"passport": passport})
            resp.raise_for_status()
            return bool(resp.json()["in_terror_list"])
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise BlackListError(str(exc)) from exc
```

- [ ] **Step 4: Добавить зависимость**

В `requirements.txt` добавить строку (после `alembic>=1.13`):

```
# HTTP-клиент для запроса к сервису чёрного списка паспортов.
httpx>=0.27
```

Установить: `pip install -r requirements.txt`

- [ ] **Step 5: Документировать env в README**

В конец `README.md` дописать раздел:

```markdown
## Чёрный список паспортов (v2)

Ручка `POST /applications/v2` перед решением проверяет паспорт по внешнему сервису:

- `BLACK_LIST_URL` — базовый адрес сервиса (по умолчанию `http://212.147.238.3:8090`).
- `BLACK_LIST_TIMEOUT_SECONDS` — таймаут запроса в секундах (по умолчанию `0.8`;
  держать заметно меньше `REQUEST_TIMEOUT_SECONDS`).

Если сервис недоступен — заявка отклоняется (fail-closed).
```

- [ ] **Step 6: Запустить тест — должен пройти**

Run: `pytest tests/test_black_list.py -v`
Expected: PASS (5 тестов).

- [ ] **Step 7: Коммит**

```bash
git add app/src/black_list.py tests/test_black_list.py requirements.txt README.md
git commit -m "feat: клиент сервиса чёрного списка паспортов (fail-closed)"
```

---

## Task 3: Функция решения `make_decision_v2`

**Files:**
- Modify: `app/src/server.py` (после `make_decision`, блок «Бизнес-логика»)
- Test: `tests/test_decision_v2.py`

**Interfaces:**
- Consumes: `ApplicationRequestV2` (Task 1), `calculate_age`, `MIN_AGE`, метрики из `metrics.py`.
- Produces:
  `def make_decision_v2(payload, *, in_black_list: bool = False, black_list_check_failed: bool = False) -> dict`
  — возвращает dict той же формы, что `make_decision` (`application_id, status, applicant, reasons, received_at`).

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_decision_v2.py`:

```python
from server import ApplicationRequestV2, make_decision_v2

BASE = dict(
    last_name="Иванов", first_name="Иван", phone="+79991234567",
    email="ivan@example.ru", passport="1234567890",
    region="Москва", loan_purpose="покупка", amount=100000,
)


def _payload(birth_date="2000-05-15"):
    return ApplicationRequestV2(**BASE, birth_date=birth_date)


def test_approved_when_adult_and_clean():
    d = make_decision_v2(_payload(), in_black_list=False, black_list_check_failed=False)
    assert d["status"] == "approved"
    assert d["reasons"] == []


def test_declined_when_underage():
    d = make_decision_v2(_payload(birth_date="2015-01-01"))
    assert d["status"] == "declined"
    assert any("меньше" in r for r in d["reasons"])


def test_declined_when_in_black_list():
    d = make_decision_v2(_payload(), in_black_list=True)
    assert d["status"] == "declined"
    assert any("чёрном списке" in r for r in d["reasons"])


def test_declined_when_check_failed():
    d = make_decision_v2(_payload(), black_list_check_failed=True)
    assert d["status"] == "declined"
    assert any("Не удалось проверить" in r for r in d["reasons"])


def test_no_upper_age_limit():
    # 40 лет — для v2 это НЕ повод к отказу (верхней границы нет).
    d = make_decision_v2(_payload(birth_date="1985-01-01"))
    assert d["status"] == "approved"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `pytest tests/test_decision_v2.py -v`
Expected: FAIL — `ImportError: cannot import name 'make_decision_v2'`.

- [ ] **Step 3: Реализовать `make_decision_v2`**

В `app/src/server.py` сразу после функции `make_decision` добавить:

```python
def make_decision_v2(
    payload: "ApplicationRequestV2",
    *,
    in_black_list: bool = False,
    black_list_check_failed: bool = False,
) -> dict:
    """Решение по заявке v2: правила — возраст < MIN_AGE и чёрный список."""
    today = date.today()
    age = calculate_age(payload.birth_date, today)
    application_id = str(uuid.uuid4())

    logger.info(
        "Заявка v2 %s: %s %s, возраст %d, регион %s",
        application_id, payload.last_name, payload.first_name, age, payload.region,
    )

    reasons = []
    if age < MIN_AGE:
        reason = f"Возраст заявителя {age} лет — меньше минимально допустимого {MIN_AGE}"
        reasons.append(reason)
        REJECTION_REASONS.labels(reason="age_below_min").inc()
        logger.info("Заявка %s — отказ: %s", application_id, reason)
    if in_black_list:
        reason = "Паспорт в чёрном списке"
        reasons.append(reason)
        REJECTION_REASONS.labels(reason="black_list").inc()
        logger.info("Заявка %s — отказ: %s", application_id, reason)
    if black_list_check_failed:
        reason = "Не удалось проверить паспорт по чёрному списку — заявка отклонена"
        reasons.append(reason)
        REJECTION_REASONS.labels(reason="black_list_check_unavailable").inc()
        logger.info("Заявка %s — отказ: %s", application_id, reason)

    status = "approved" if not reasons else "declined"
    DECISIONS.labels(status=status, country="-").inc()
    if payload.amount is not None:
        APPLICATION_AMOUNT_RUB.observe(payload.amount)
    logger.info(
        "Заявка %s — итог: %s", application_id, status.upper(),
        extra={"application_id": application_id, "status": status},
    )

    full_name = " ".join(
        part for part in (payload.last_name, payload.first_name, payload.middle_name) if part
    )
    return {
        "application_id": application_id,
        "status": status,
        "applicant": {
            "full_name": full_name,
            "age": age,
            "phone": payload.phone,
        },
        "reasons": reasons,
        "received_at": datetime.now().isoformat(timespec="seconds"),
    }
```

- [ ] **Step 4: Запустить тест — должен пройти**

Run: `pytest tests/test_decision_v2.py -v`
Expected: PASS (5 тестов).

- [ ] **Step 5: Коммит**

```bash
git add app/src/server.py tests/test_decision_v2.py
git commit -m "feat: решение по заявке v2 (возраст 18+ и чёрный список)"
```

---

## Task 4: Персистентность v2-полей (модель + repository + миграция)

**Files:**
- Modify: `app/src/models.py:41-53` (класс `Application`)
- Modify: `app/src/repository.py:48-66` (`save_application`)
- Create: `alembic/versions/0002_applications_v2.py`
- Test: `tests/test_persistence_v2.py`

**Interfaces:**
- Consumes: `save_application` (расширяется), `Application` (новые колонки).
- Produces: `save_application(..., email=None, passport=None, region=None, loan_purpose=None)`; `country` теперь `str | None`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_persistence_v2.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import select

from models import Application
from repository import get_or_create_user, save_application


async def test_save_application_persists_v2_fields(db_session):
    user = await get_or_create_user(
        db_session, last_name="Иванов", first_name="Иван", middle_name="",
        birth_date=datetime(2000, 5, 15).date(), phone="+79991234567",
    )
    app_id = uuid.uuid4()
    await save_application(
        db_session, application_id=app_id, user=user, amount=100000,
        country=None, status="approved", reasons=[], received_at=datetime.now(),
        email="ivan@example.ru", passport="1234567890",
        region="Москва", loan_purpose="покупка",
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(Application).where(Application.application_id == app_id)
        )
    ).scalar_one()
    assert row.email == "ivan@example.ru"
    assert row.passport == "1234567890"
    assert row.region == "Москва"
    assert row.loan_purpose == "покупка"
    assert row.country is None
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `pytest tests/test_persistence_v2.py -v`
Expected: FAIL — `TypeError: save_application() got an unexpected keyword argument 'email'`.

- [ ] **Step 3: Добавить колонки в модель**

В `app/src/models.py`, класс `Application`, изменить `country` и добавить v2-поля. Заменить строку `country`:

```python
    country: Mapped[str | None] = mapped_column(String, nullable=True)
```

и сразу после строки `received_at: Mapped[datetime] = ...` (перед `created_at`) добавить:

```python
    # Поля заявки v2 (nullable — у заявок v1 остаются NULL).
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    passport: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    loan_purpose: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: Расширить `save_application`**

В `app/src/repository.py` заменить сигнатуру и тело `save_application`:

```python
async def save_application(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    user: User,
    amount: float | None,
    country: str | None,
    status: str,
    reasons: list[str],
    received_at: datetime,
    email: str | None = None,
    passport: str | None = None,
    region: str | None = None,
    loan_purpose: str | None = None,
) -> Application:
    """Создать заявку, привязанную к пользователю."""
    application = Application(
        application_id=application_id, user=user, amount=amount,
        country=country, status=status, reasons=reasons, received_at=received_at,
        email=email, passport=passport, region=region, loan_purpose=loan_purpose,
    )
    session.add(application)
    await session.flush()
    return application
```

- [ ] **Step 5: Создать миграцию Alembic**

Создать `alembic/versions/0002_applications_v2.py`:

```python
"""applications: поля v2 + country nullable

Revision ID: 0002_applications_v2
Revises: 0001_initial
Create Date: 2026-06-27
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_applications_v2"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("email", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("passport", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("region", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("loan_purpose", sa.String(), nullable=True))
    op.alter_column(
        "applications", "country", existing_type=sa.String(), nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "applications", "country", existing_type=sa.String(), nullable=False,
    )
    op.drop_column("applications", "loan_purpose")
    op.drop_column("applications", "region")
    op.drop_column("applications", "passport")
    op.drop_column("applications", "email")
```

- [ ] **Step 6: Запустить тест — должен пройти**

Run: `pytest tests/test_persistence_v2.py -v`
Expected: PASS.

- [ ] **Step 7: Регрессия — весь набор зелёный, миграция импортируется**

Run: `pytest -q`
Expected: PASS (включая `tests/test_persistence.py`, `tests/test_repository.py`).

Run: `python -c "import importlib.util, pathlib; importlib.util.spec_from_file_location('m', 'alembic/versions/0002_applications_v2.py')"`
Expected: без ошибок (синтаксис миграции валиден).

> Примечание: тестовая БД создаётся через `Base.metadata.create_all`, поэтому миграцию 0002 набор тестов не применяет. На Postgres её надо прогнать вручную (`alembic upgrade head`) — это вне рамок тестов (см. спеку).

- [ ] **Step 8: Коммит**

```bash
git add app/src/models.py app/src/repository.py alembic/versions/0002_applications_v2.py tests/test_persistence_v2.py
git commit -m "feat: хранение полей заявки v2 и миграция (country → nullable)"
```

---

## Task 5: Ручка `POST /applications/v2` + расширение мидлваров

**Files:**
- Modify: `app/src/request_timeout.py:14-26`
- Modify: `app/src/ratelimit.py:46-61`
- Modify: `app/src/server.py` (импорт `black_list`, установка мидлваров, новая ручка)
- Test: `tests/test_http_v2.py`

**Interfaces:**
- Consumes: `check_passport`, `BlackListError` (Task 2); `ApplicationRequestV2` (Task 1); `make_decision_v2` (Task 3); `save_application` (Task 4).
- Produces: эндпоинт `POST /applications/v2` (`response_model=ApplicationDecision`).
- Изменённые сигнатуры мидлваров:
  - `install_request_timeout(app, *, seconds, counter, paths=("/applications",), method="POST")`
  - `install_rate_limiter(app, *, bucket, counter, paths=("/applications",), method="POST")`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_http_v2.py`:

```python
import pytest

import server

VALID = {
    "last_name": "Иванов", "first_name": "Иван", "phone": "+79991234567",
    "birth_date": "2000-05-15", "email": "ivan@example.ru",
    "passport": "1234567890", "region": "Москва",
    "loan_purpose": "покупка", "amount": 100000,
}


@pytest.fixture
def clean_blacklist(monkeypatch):
    async def fake(passport):
        return False
    monkeypatch.setattr(server, "check_passport", fake)


async def test_v2_approved(async_client, clean_blacklist):
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


async def test_v2_declined_in_black_list(async_client, monkeypatch):
    async def fake(passport):
        return True
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("чёрном списке" in r for r in body["reasons"])


async def test_v2_fail_closed_when_service_down(async_client, monkeypatch):
    async def fake(passport):
        raise server.BlackListError("down")
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Не удалось проверить" in r for r in body["reasons"])


async def test_v2_underage(async_client, clean_blacklist):
    resp = await async_client.post(
        "/applications/v2", json={**VALID, "birth_date": "2015-01-01"}
    )
    assert resp.json()["status"] == "declined"


async def test_v2_missing_passport_422(async_client, clean_blacklist):
    body = {k: v for k, v in VALID.items() if k != "passport"}
    resp = await async_client.post("/applications/v2", json=body)
    assert resp.status_code == 422


async def test_v2_persisted(async_client, clean_blacklist):
    import db
    from sqlalchemy import select

    from models import Application

    await async_client.post("/applications/v2", json=VALID)
    async with db.AsyncSessionLocal() as s:
        row = (await s.execute(select(Application))).scalars().first()
    assert row is not None
    assert row.passport == "1234567890"
    assert row.email == "ivan@example.ru"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `pytest tests/test_http_v2.py -v`
Expected: FAIL — 404 на `/applications/v2` (или `AttributeError` на `server.check_passport`).

- [ ] **Step 3: Расширить мидлвар таймаута на набор путей**

В `app/src/request_timeout.py` заменить сигнатуру и проверку:

```python
def install_request_timeout(app: FastAPI, *, seconds: float, counter,
                            paths: tuple[str, ...] = ("/applications",),
                            method: str = "POST") -> None:
    """Вешает на `app` middleware: ограничивает время method+paths, иначе 503."""

    @app.middleware("http")
    async def _timeout(request: Request, call_next):
        if request.method == method and request.url.path in paths:
            try:
                return await asyncio.wait_for(call_next(request), timeout=seconds)
            except asyncio.TimeoutError:
                counter.inc()
                return JSONResponse({"detail": "request timeout"}, status_code=503)
        return await call_next(request)
```

- [ ] **Step 4: Расширить мидлвар rate-limit на набор путей**

В `app/src/ratelimit.py` заменить сигнатуру и проверку `install_rate_limiter`:

```python
def install_rate_limiter(app: FastAPI, *, bucket: TokenBucket, counter,
                         paths: tuple[str, ...] = ("/applications",),
                         method: str = "POST") -> None:
    """Вешает на `app` middleware: лимитирует method+paths, иначе 429."""

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.method == method and request.url.path in paths:
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

- [ ] **Step 5: Подключить v2 в `server.py`**

В `app/src/server.py` добавить импорт после `from repository import ...`:

```python
from black_list import BlackListError, check_passport
```

В вызовах установки мидлваров передать оба пути. Заменить блок установки:

```python
if _REQUEST_TIMEOUT_SECONDS > 0:
    install_request_timeout(
        app, seconds=_REQUEST_TIMEOUT_SECONDS, counter=REQUEST_TIMEOUTS,
        paths=("/applications", "/applications/v2"),
    )
if _RATE_LIMIT_RPS > 0:
    install_rate_limiter(
        app,
        bucket=TokenBucket(_RATE_LIMIT_RPS, _RATE_LIMIT_BURST),
        counter=RATE_LIMITED,
        paths=("/applications", "/applications/v2"),
    )
```

После функции-ручки `create_application` добавить ручку v2:

```python
@app.post("/applications/v2", response_model=ApplicationDecision)
async def create_application_v2(
    payload: ApplicationRequestV2,
    session: AsyncSession = Depends(get_session),
):
    try:
        in_black_list = await check_passport(payload.passport)
        check_failed = False
    except BlackListError:
        logger.warning("Чёрный список недоступен — заявка отклонена (fail-closed)")
        in_black_list, check_failed = False, True

    decision = make_decision_v2(
        payload, in_black_list=in_black_list, black_list_check_failed=check_failed,
    )
    try:
        user = await get_or_create_user(
            session,
            last_name=payload.last_name,
            first_name=payload.first_name,
            middle_name=payload.middle_name,
            birth_date=payload.birth_date,
            phone=payload.phone,
        )
        await save_application(
            session,
            application_id=uuid.UUID(decision["application_id"]),
            user=user,
            amount=payload.amount,
            country=None,
            status=decision["status"],
            reasons=decision["reasons"],
            received_at=datetime.fromisoformat(decision["received_at"]),
            email=payload.email,
            passport=payload.passport,
            region=payload.region,
            loan_purpose=payload.loan_purpose,
        )
    except SQLAlchemyError as exc:
        logger.exception("Не удалось сохранить заявку %s", decision["application_id"])
        raise HTTPException(status_code=500, detail="Ошибка сохранения заявки") from exc
    return decision
```

- [ ] **Step 6: Запустить тест v2 — должен пройти**

Run: `pytest tests/test_http_v2.py -v`
Expected: PASS (6 тестов).

- [ ] **Step 7: Полная регрессия**

Run: `pytest -q`
Expected: PASS — весь набор, включая `tests/test_timeout.py`, `tests/test_ratelimit.py`, `tests/test_http.py` (v1 не затронут).

- [ ] **Step 8: Коммит**

```bash
git add app/src/server.py app/src/request_timeout.py app/src/ratelimit.py tests/test_http_v2.py
git commit -m "feat: ручка POST /applications/v2 с проверкой паспорта по чёрному списку"
```

---

## Финальная проверка

- [ ] `pytest -q` — весь набор зелёный.
- [ ] Ручная проверка живьём (опционально, нужна БД): поднять сервер, дёрнуть
  `POST /applications/v2` с реальным паспортом и убедиться, что `approved`
  при чистом паспорте; при выключенном `BLACK_LIST_URL` — `declined` (fail-closed).
- [ ] Сверить покрытие со спекой: ручка v2, поля, правила (возраст<18 + чёрный
  список), fail-closed, миграция, мидлвары — всё реализовано.
