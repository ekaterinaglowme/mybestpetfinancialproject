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
