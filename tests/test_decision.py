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


def test_request_extra_field_rejected():
    # Неизвестное поле должно отклоняться (extra="forbid"), а не игнорироваться.
    with pytest.raises(Exception):
        _valid_request(unexpected_field="garbage")


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
