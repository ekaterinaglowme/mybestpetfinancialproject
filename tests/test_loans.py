"""Юнит-тесты чистой логики займа: дисконтная ставка, долг, статус.

Время передаётся аргументом — проверки детерминированы, без БД и без «сегодня».
"""

from datetime import date
from decimal import Decimal

import pytest

import loans


@pytest.mark.parametrize("days,expected", [
    (0, 0.10), (7, 0.10),      # порог 0–7 дней
    (8, 0.05), (30, 0.05),     # порог 8–30 дней
    (31, 0.02), (365, 0.02),   # > 30 дней
])
def test_rate_for_age(days, expected):
    assert loans.rate_for_age(days) == expected


def test_amount_owed_each_tier():
    assert loans.amount_owed(100000, 0) == 110000.0    # +10%
    assert loans.amount_owed(100000, 10) == 105000.0   # +5%
    assert loans.amount_owed(100000, 40) == 102000.0   # +2%


def test_amount_owed_accepts_decimal():
    # из БД сумма приходит как Decimal (Numeric) — должно считаться без ошибок
    assert loans.amount_owed(Decimal("99.99"), 0) == 109.99


def test_loan_view_active_uses_today():
    v = loans.loan_view(
        application_id="abc", amount=100000, status="выдано",
        issued_at=date(2026, 6, 1), repaid_at=None, today=date(2026, 6, 6),  # 5 дней
    )
    assert v["status"] == "выдано"
    assert v["days_elapsed"] == 5
    assert v["current_rate"] == 0.10
    assert v["amount_owed"] == 110000.0
    assert v["repaid_at"] is None
    assert v["application_id"] == "abc"


def test_loan_view_repaid_is_frozen():
    v = loans.loan_view(
        application_id="abc", amount=100000, status="вернули",
        issued_at=date(2026, 6, 1), repaid_at=date(2026, 6, 20),  # 19 дней → 5%
        today=date(2026, 12, 31),  # сильно позже — но долг заморожен на дату возврата
    )
    assert v["status"] == "вернули"
    assert v["days_elapsed"] == 19
    assert v["current_rate"] == 0.05
    assert v["amount_owed"] == 105000.0
    assert v["repaid_at"] == date(2026, 6, 20)


def test_loan_view_passes_status_through():
    # Статус теперь хранится в БД — loan_view не вычисляет его, а отдаёт как есть.
    # Даже при пустом repaid_at статус может быть «не вернули» (списание).
    v = loans.loan_view(
        application_id="abc", amount=100000, status="не вернули",
        issued_at=date(2026, 6, 1), repaid_at=None, today=date(2026, 6, 6),
    )
    assert v["status"] == "не вернули"
