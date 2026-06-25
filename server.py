"""PetBank — простейший сервер приёма заявок.

Запуск:  python server.py   (или  python server.py 8080  — другой порт)

Эндпоинты:
    POST /applications  — подать заявку, вернёт решение approved / declined
    GET  /health        — проверка, что сервер жив
    GET  /              — короткая справка
    GET  /docs          — Swagger UI (интерактивная документация)
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import date, datetime

import uvicorn
from fastapi import FastAPI, Request
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, ConfigDict, field_validator

from logging_setup import request_id_ctx, setup_logging
from metrics import APPLICATION_AMOUNT_RUB, DECISIONS, REJECTION_REASONS

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("petbank.access")

# Настраиваем JSON-логирование при импорте — чтобы оно работало и под тестами,
# которые импортируют app напрямую, не вызывая run().
setup_logging()

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
    application_id = str(uuid.uuid4())

    logger.info(
        "Заявка %s: %s %s, возраст %d, страна %s",
        application_id, payload.last_name, payload.first_name, age, payload.country,
    )

    reasons = []
    if age < MIN_AGE:
        reason = f"Возраст заявителя {age} лет — меньше минимально допустимого {MIN_AGE}"
        reasons.append(reason)
        REJECTION_REASONS.labels(reason="age_below_min").inc()
        logger.info("Заявка %s — отказ: %s", application_id, reason)
    if age > MAX_AGE:
        reason = f"Возраст заявителя {age} лет — больше макс допустимого {MAX_AGE}"
        reasons.append(reason)
        REJECTION_REASONS.labels(reason="age_above_max").inc()
        logger.info("Заявка %s — отказ: %s", application_id, reason)
    if payload.country.lower() in BLOCKED_COUNTRIES:
        reason = f"Заявки из страны «{payload.country}» не принимаются"
        reasons.append(reason)
        REJECTION_REASONS.labels(reason="blocked_country").inc()
        logger.info("Заявка %s — отказ: %s", application_id, reason)

    status = "approved" if not reasons else "declined"
    DECISIONS.labels(status=status, country=payload.country.strip().lower()).inc()
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

app = FastAPI(title="PetBank")

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
    setup_logging()
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    cli_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(port=cli_port)
