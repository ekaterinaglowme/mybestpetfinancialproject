"""PetBank — простейший сервер приёма заявок.

Зависимостей нет: только стандартная библиотека Python (http.server).
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


# --- HTTP-слой -------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "PetBank/0.1"

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/") or "/"
        if path == "/health":
            self._send_json(200, {"status": "ok"})
        elif path == "/":
            self._send_json(200, {
                "service": "PetBank",
                "endpoints": ["POST /applications", "GET /health"],
                "rule": f"возраст >= {MIN_AGE}",
            })
        else:
            self._send_json(404, {"error": "not_found", "message": f"Неизвестный путь: {self.path}"})

    def do_POST(self):
        if self.path.rstrip("/") != "/applications":
            self._send_json(404, {"error": "not_found", "message": f"Неизвестный путь: {self.path}"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0

        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            self._send_json(400, {"error": "bad_request", "message": "Пустое тело запроса"})
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "bad_request", "message": "Тело запроса не является валидным JSON"})
            return

        cleaned, errors = validate_payload(payload)
        if errors:
            self._send_json(400, {
                "error": "validation_error",
                "message": "Проверьте поля заявки",
                "details": errors,
            })
            return

        self._send_json(200, make_decision(cleaned))

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def run(host="0.0.0.0", port=None):
    port = port or int(os.environ.get("PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"PetBank запущен: http://localhost:{port}  (Ctrl+C — остановить)")
    print(f"Правило одобрения: возраст заявителя >= {MIN_AGE} лет")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    cli_port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(port=cli_port)
