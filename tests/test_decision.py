"""Юнит-тесты бизнес-логики: возраст, валидация, решение.

Чистые функции из server.py, без HTTP — гоняются мгновенно.
"""

from datetime import date, timedelta

import pytest

from server import MAX_AGE, MIN_AGE, calculate_age, make_decision, validate_payload


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


# --- validate_payload ------------------------------------------------------

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


def test_validate_ok():
    cleaned, errors = validate_payload(_valid_payload())
    assert errors == []
    assert cleaned["last_name"] == "Иванов"
    assert cleaned["birth_date"] == date(1990, 5, 15)
    assert cleaned["amount"] == 100000


def test_validate_body_not_object():
    cleaned, errors = validate_payload(["not", "a", "dict"])
    assert cleaned is None
    assert errors and errors[0]["field"] == "<body>"


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


def test_validate_strips_whitespace():
    cleaned, errors = validate_payload(_valid_payload(first_name="  Иван  "))
    assert errors == []
    assert cleaned["first_name"] == "Иван"


def test_validate_middle_name_optional():
    payload = _valid_payload()
    del payload["middle_name"]
    cleaned, errors = validate_payload(payload)
    assert errors == []
    assert cleaned["middle_name"] == ""


def test_validate_middle_name_wrong_type():
    _, errors = validate_payload(_valid_payload(middle_name=123))
    assert any(e["field"] == "middle_name" for e in errors)


def test_validate_birth_date_bad_format():
    _, errors = validate_payload(_valid_payload(birth_date="15.05.1990"))
    assert any(e["field"] == "birth_date" for e in errors)


def test_validate_birth_date_in_future():
    future = (date.today() + timedelta(days=1)).isoformat()
    _, errors = validate_payload(_valid_payload(birth_date=future))
    assert any(e["field"] == "birth_date" for e in errors)


def test_validate_amount_optional():
    payload = _valid_payload()
    del payload["amount"]
    cleaned, errors = validate_payload(payload)
    assert errors == []
    assert "amount" not in cleaned


def test_validate_amount_bool_rejected():
    # bool — подкласс int, должен отсекаться явно.
    _, errors = validate_payload(_valid_payload(amount=True))
    assert any(e["field"] == "amount" for e in errors)


def test_validate_amount_negative_rejected():
    _, errors = validate_payload(_valid_payload(amount=-1))
    assert any(e["field"] == "amount" for e in errors)


# --- make_decision ---------------------------------------------------------

def _cleaned(birth_date, country="Россия"):
    return {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        "phone": "+79991234567",
        "country": country,
        "birth_date": birth_date,
    }


def test_decision_adult_approved():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_cleaned(born))
    assert result["status"] == "approved"
    assert result["reasons"] == []
    assert result["applicant"]["age"] == 30


def test_decision_minor_declined():
    born = date.today().replace(year=date.today().year - 10)
    result = make_decision(_cleaned(born))
    assert result["status"] == "declined"
    assert len(result["reasons"]) == 1
    assert str(MIN_AGE) in result["reasons"][0]


def test_decision_full_name_with_middle():
    born = date.today().replace(year=date.today().year - 30)
    result = make_decision(_cleaned(born))
    assert result["applicant"]["full_name"] == "Иванов Иван Иванович"


def test_decision_full_name_without_middle():
    cleaned = _cleaned(date.today().replace(year=date.today().year - 30))
    cleaned["middle_name"] = ""
    result = make_decision(cleaned)
    assert result["applicant"]["full_name"] == "Иванов Иван"


def test_decision_has_application_id():
    result = make_decision(_cleaned(date(1990, 5, 15)))
    assert result["application_id"]


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
