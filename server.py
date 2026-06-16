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
