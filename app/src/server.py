"""PetBank — простейший сервер приёма заявок.

Запуск:  python app/src/main.py   (или  python app/src/server.py 8080  — другой порт)

Эндпоинты:
    POST /applications/v2  — подать заявку, вернёт решение approved / declined
    GET  /health           — проверка, что сервер жив
    GET  /                 — короткая справка
    GET  /docs             — Swagger UI (интерактивная документация)
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import db
import loans
from db import get_session
from repository import create_loan, get_loan, get_or_create_user, save_application
import black_list
from black_list import BlackListError, check_passport

from logging_setup import request_id_ctx, setup_logging
from metrics import (APPLICATION_AMOUNT_RUB, DB_WRITE_SECONDS, DECISIONS,
                     RATE_LIMITED, REJECTION_REASONS)
from ratelimit import TokenBucket, install_rate_limiter

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("petbank.access")

# Настраиваем JSON-логирование при импорте — чтобы оно работало и под тестами,
# которые импортируют app напрямую, не вызывая run().
setup_logging()

# --- Бизнес-правила --------------------------------------------------------

# Заявителю должно быть не меньше MIN_AGE лет.
MIN_AGE = 18


# --- Pydantic-модели -------------------------------------------------------

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


class LoanStatus(BaseModel):
    application_id: str
    amount: float
    issued_at: date
    status: str               # выдано / вернули / не вернули / ошибка
    days_elapsed: int
    current_rate: float
    amount_owed: float
    repaid_at: date | None = None


class RepayRequest(BaseModel):
    """Тело /loans/{id}/repay — фиксация исхода. По умолчанию «вернули» сегодня.

    `ошибка` оператором не принимается — это системный статус.
    """
    outcome: Literal["вернули", "не вернули"] = "вернули"
    repaid_at: date | None = None  # только для «вернули»; пусто = сегодня


# --- Бизнес-логика ---------------------------------------------------------

def calculate_age(birth_date: date, today: date) -> int:
    """Полное число лет на дату `today`."""
    years = today.year - birth_date.year
    # День рождения в этом году ещё не наступил — вычитаем год.
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


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


# --- HTTP-слой -------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    own = db.AsyncSessionLocal is None       # в тестах уже сконфигурировано на SQLite
    if own:
        # Пул выше пикового числа одновременных заявок (sim шлёт пачками) +
        # таймауты на пул и на запрос (engine_options): залипший запрос или
        # исчерпание пула не должны подвешивать воркер навсегда.
        db.configure(db.build_dsn(), **db.engine_options())
    own_bl = black_list._client is None      # в тестах клиент инжектят отдельно
    if own_bl:
        black_list.configure()
    yield
    if own_bl:
        await black_list.dispose()
    if own:
        await db.dispose()


app = FastAPI(title="PetBank", lifespan=lifespan)

# --- Защита /applications/v2 под нагрузкой (env-конфиг; 0 = выключить) ---
_RATE_LIMIT_RPS = float(os.environ.get("RATE_LIMIT_RPS", "100"))
_RATE_LIMIT_BURST = float(os.environ.get("RATE_LIMIT_BURST") or _RATE_LIMIT_RPS)

if _RATE_LIMIT_RPS > 0:
    install_rate_limiter(
        app,
        bucket=TokenBucket(_RATE_LIMIT_RPS, _RATE_LIMIT_BURST),
        counter=RATE_LIMITED,
        paths=("/applications/v2",),
    )

# Prometheus-метрики: instrumentator сам поднимает GET /metrics и считает
# HTTP-метрики (rate / errors / latency по ручкам).
Instrumentator().instrument(app).expose(app)


# Максимум байт тела запроса, попадающих в лог (защита от распухания логов).
MAX_BODY_BYTES = 10240


def _body_for_log(body_bytes: bytes) -> dict:
    """Готовит поле body для лога: JSON, иначе сырой текст; пусто → {}."""
    if not body_bytes:
        return {}
    chunk = body_bytes[:MAX_BODY_BYTES]
    try:
        return {"body": json.loads(chunk)}
    except (ValueError, UnicodeDecodeError):
        return {"body": chunk.decode("utf-8", errors="replace")}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_ctx.set(request_id)
    try:
        body_bytes = await request.body()
        start = time.perf_counter()
        status_code = 500
        exc: Exception | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as error:  # noqa: BLE001 — логируем и пробрасываем
            exc = error
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        access_logger.info(
            "%s %s %s", request.method, request.url.path, status_code,
            extra={
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
                **_body_for_log(body_bytes),
            },
            exc_info=exc,
        )
        if exc is not None:
            raise exc
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_ctx.reset(token)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "service": "PetBank",
        "endpoints": ["POST /applications/v2", "GET /health", "GET /docs"],
        "rule": f"возраст от {MIN_AGE}, паспорт не в чёрном списке",
    }


@app.get("/loans/{application_id}", response_model=LoanStatus)
async def get_loan_status(
    application_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    loan = await get_loan(session, application_id)
    if loan is None:
        raise HTTPException(status_code=404, detail="Заём не найден")
    return loans.loan_view(
        application_id=loan.application_id, amount=loan.amount, status=loan.status,
        issued_at=loan.issued_at, repaid_at=loan.repaid_at, today=date.today(),
    )


async def _mark_loan_error(application_id: uuid.UUID) -> None:
    """Best-effort: пометить заём статусом «ошибка» отдельной транзакцией.

    Если БД недоступна целиком — записать не выйдет; это принятое ограничение
    (статус сам себя при полном отказе БД зафиксировать не может), а не баг.
    """
    try:
        async with db.transaction() as s:
            loan = await get_loan(s, application_id)
            if loan is not None:
                loan.status = "ошибка"
    except SQLAlchemyError:
        logger.exception("Не удалось записать «ошибка» для займа %s", application_id)


@app.post("/loans/{application_id}/repay", response_model=LoanStatus)
async def repay_loan(
    application_id: uuid.UUID,
    body: RepayRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    req = body or RepayRequest()
    loan = await get_loan(session, application_id)
    if loan is None:
        raise HTTPException(status_code=404, detail="Заём не найден")
    if loan.status != "выдано":
        raise HTTPException(status_code=409, detail="Заём уже закрыт")

    today = date.today()
    if req.outcome == "вернули":
        repaid_at = req.repaid_at or today
        if repaid_at < loan.issued_at:
            raise HTTPException(status_code=422, detail="Дата возврата раньше даты выдачи")
        if repaid_at > today:
            raise HTTPException(status_code=422, detail="Дата возврата не может быть в будущем")
        loan.status = "вернули"
        loan.repaid_at = repaid_at
    else:  # «не вернули» — денег не было, дата возврата неприменима
        if req.repaid_at is not None:
            raise HTTPException(
                status_code=422, detail="У «не вернули» не может быть даты возврата"
            )
        loan.status = "не вернули"

    try:
        with DB_WRITE_SECONDS.labels(operation="repay").time():
            await session.flush()
    except SQLAlchemyError as exc:
        logger.exception("Сбой фиксации исхода займа %s — помечаю «ошибка»", application_id)
        await _mark_loan_error(application_id)
        raise HTTPException(
            status_code=500, detail="Не удалось зафиксировать исход"
        ) from exc
    return loans.loan_view(
        application_id=loan.application_id, amount=loan.amount, status=loan.status,
        issued_at=loan.issued_at, repaid_at=loan.repaid_at, today=today,
    )


@app.post("/applications/v2", response_model=ApplicationDecision)
async def create_application_v2(payload: ApplicationRequestV2):
    # Чёрный список дёргаем ДО захвата сессии БД: иначе соединение из пула держится
    # открытым весь внешний вызов, и под залпом заявок пул копит очередь.
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
        async with db.transaction() as session:
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
            # Одобрение с суммой = выдача займа (ключ — тот же application_id).
            if decision["status"] == "approved" and payload.amount:
                await create_loan(
                    session,
                    application_id=uuid.UUID(decision["application_id"]),
                    amount=payload.amount,
                    issued_at=date.today(),
                )
    except SQLAlchemyError as exc:
        logger.exception("Не удалось сохранить заявку %s", decision["application_id"])
        raise HTTPException(status_code=500, detail="Ошибка сохранения заявки") from exc
    return decision


def run(host="0.0.0.0", port=None):
    port = port or int(os.environ.get("PORT", "8000"))
    print(f"PetBank запущен: http://localhost:{port}  (Ctrl+C — остановить)")
    print(f"Swagger UI:       http://localhost:{port}/docs")
    print(f"Правило одобрения: возраст от {MIN_AGE} лет, паспорт не в чёрном списке")
    setup_logging()
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    cli_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(port=cli_port)
