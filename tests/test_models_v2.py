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
