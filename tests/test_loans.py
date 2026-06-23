"""Юнит-тесты учёта займов: ставка, расчёт долга, хранилище, статус.

Чистая логика из loans.py, без HTTP — время передаётся аргументом, поэтому
проверки детерминированы (не зависят от «сегодня»).
"""

from datetime import date

import pytest

import loans


@pytest.fixture(autouse=True)
def _clean_store():
    loans.reset()
    yield
    loans.reset()


# --- rate_for_age ----------------------------------------------------------

@pytest.mark.parametrize("days,expected", [
    (0, 0.10), (7, 0.10),      # порог 0–7 дней
    (8, 0.05), (30, 0.05),     # порог 8–30 дней
    (31, 0.02), (365, 0.02),   # > 30 дней
])
def test_rate_for_age(days, expected):
    assert loans.rate_for_age(days) == expected


# --- amount_owed -----------------------------------------------------------

def test_amount_owed_each_tier():
    assert loans.amount_owed(100000, 0) == 110000.0    # +10%
    assert loans.amount_owed(100000, 10) == 105000.0   # +5%
    assert loans.amount_owed(100000, 40) == 102000.0   # +2%


def test_amount_owed_rounds_to_kopecks():
    # 99.99 * 1.10 = 109.989 → округляем до копеек
    assert loans.amount_owed(99.99, 0) == 109.99


# --- create_loan / get_loan ------------------------------------------------

def test_create_and_get_loan():
    loan = loans.create_loan("L1", 50000, date(2026, 6, 1))
    assert loan.loan_id == "L1"
    assert loan.amount == 50000
    assert loan.issued_at == date(2026, 6, 1)
    assert loan.repaid_at is None
    assert loans.get_loan("L1") is loan


def test_get_unknown_loan_returns_none():
    assert loans.get_loan("nope") is None


# --- mark_repaid -----------------------------------------------------------

def test_mark_repaid_sets_date():
    loans.create_loan("L1", 50000, date(2026, 6, 1))
    loan = loans.mark_repaid("L1", date(2026, 6, 10))
    assert loan.repaid_at == date(2026, 6, 10)


def test_mark_repaid_twice_raises():
    loans.create_loan("L1", 50000, date(2026, 6, 1))
    loans.mark_repaid("L1", date(2026, 6, 10))
    with pytest.raises(loans.AlreadyRepaid):
        loans.mark_repaid("L1", date(2026, 6, 11))


def test_mark_repaid_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        loans.mark_repaid("nope", date(2026, 6, 10))


# --- loan_view (статус + текущий долг) -------------------------------------

def test_loan_view_active_uses_today():
    loans.create_loan("L1", 100000, date(2026, 6, 1))
    view = loans.loan_view(loans.get_loan("L1"), date(2026, 6, 6))  # 5 дней
    assert view["status"] == "не отдал"
    assert view["days_elapsed"] == 5
    assert view["current_rate"] == 0.10
    assert view["amount_owed"] == 110000.0
    assert view["repaid_at"] is None


def test_loan_view_repaid_is_frozen_at_repaid_date():
    loans.create_loan("L1", 100000, date(2026, 6, 1))
    loans.mark_repaid("L1", date(2026, 6, 20))  # 19 дней → порог 8–30 → 5%
    # даже если «сегодня» сильно позже — долг заморожен на дату возврата
    view = loans.loan_view(loans.get_loan("L1"), date(2026, 12, 31))
    assert view["status"] == "отдал"
    assert view["days_elapsed"] == 19
    assert view["current_rate"] == 0.05
    assert view["amount_owed"] == 105000.0
    assert view["repaid_at"] == date(2026, 6, 20)
