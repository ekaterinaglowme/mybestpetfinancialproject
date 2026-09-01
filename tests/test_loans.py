"""Юнит-тесты чистой логики займа: ставка от времени, водораздел сеток, долг.

Время передаётся аргументом — проверки детерминированы, без БД и без «сегодня».
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

import loans

# Даты по разные стороны водораздела NEW_RATE_SINCE (2026-09-01).
NEW = date(2026, 9, 10)     # заём по новой сетке (растущая ставка)
LEGACY = date(2026, 6, 1)   # заём по старой «дисконтной» сетке


@pytest.mark.parametrize("days,expected", [
    (0, 0.20), (7, 0.20),      # порог 0–7 дней
    (8, 0.35), (30, 0.35),     # порог 8–30 дней
    (31, 0.55), (365, 0.55),   # > 30 дней
])
def test_rate_for_age_new_grid(days, expected):
    assert loans.rate_for_age(days, issued_at=NEW) == expected


@pytest.mark.parametrize("days,expected", [
    (0, 0.10), (7, 0.10),      # порог 0–7 дней
    (8, 0.05), (30, 0.05),     # порог 8–30 дней
    (31, 0.02), (365, 0.02),   # > 30 дней
])
def test_rate_for_age_legacy_grid(days, expected):
    # Займы, выданные до водораздела, живут по старым условиям.
    assert loans.rate_for_age(days, issued_at=LEGACY) == expected


def test_rate_switch_boundary():
    # Выдан ровно в день водораздела — уже новая сетка; днём раньше — старая.
    switch = loans.NEW_RATE_SINCE
    assert loans.rate_for_age(0, issued_at=switch) == 0.20
    assert loans.rate_for_age(0, issued_at=switch - timedelta(days=1)) == 0.10


def test_amount_owed_each_tier_new_grid():
    assert loans.amount_owed(100000, 0, issued_at=NEW) == 120000.0    # +20%
    assert loans.amount_owed(100000, 10, issued_at=NEW) == 135000.0   # +35%
    assert loans.amount_owed(100000, 40, issued_at=NEW) == 155000.0   # +55%


def test_amount_owed_each_tier_legacy_grid():
    assert loans.amount_owed(100000, 0, issued_at=LEGACY) == 110000.0    # +10%
    assert loans.amount_owed(100000, 10, issued_at=LEGACY) == 105000.0   # +5%
    assert loans.amount_owed(100000, 40, issued_at=LEGACY) == 102000.0   # +2%


def test_amount_owed_accepts_decimal():
    # из БД сумма приходит как Decimal (Numeric) — должно считаться без ошибок
    assert loans.amount_owed(Decimal("99.99"), 0, issued_at=NEW) == 119.99


def test_loan_view_active_uses_today():
    v = loans.loan_view(
        application_id="abc", amount=100000, status="выдано",
        issued_at=NEW, repaid_at=None, today=NEW + timedelta(days=5),
    )
    assert v["status"] == "выдано"
    assert v["days_elapsed"] == 5
    assert v["current_rate"] == 0.20
    assert v["amount_owed"] == 120000.0
    assert v["repaid_at"] is None
    assert v["application_id"] == "abc"


def test_loan_view_repaid_is_frozen():
    v = loans.loan_view(
        application_id="abc", amount=100000, status="вернули",
        issued_at=NEW, repaid_at=NEW + timedelta(days=19),  # 19 дней → 35%
        today=date(2026, 12, 31),  # сильно позже — но долг заморожен на дату возврата
    )
    assert v["status"] == "вернули"
    assert v["days_elapsed"] == 19
    assert v["current_rate"] == 0.35
    assert v["amount_owed"] == 135000.0
    assert v["repaid_at"] == NEW + timedelta(days=19)


def test_loan_view_legacy_loan_keeps_old_rate():
    # Старый заём: и после смены сетки долг считается по исторической ставке.
    v = loans.loan_view(
        application_id="abc", amount=100000, status="выдано",
        issued_at=LEGACY, repaid_at=None, today=date(2026, 9, 15),  # возраст 106 дн
    )
    assert v["current_rate"] == 0.02
    assert v["amount_owed"] == 102000.0


def test_loan_view_passes_status_through():
    # Статус теперь хранится в БД — loan_view не вычисляет его, а отдаёт как есть.
    # Даже при пустом repaid_at статус может быть «не вернули» (списание).
    v = loans.loan_view(
        application_id="abc", amount=100000, status="не вернули",
        issued_at=NEW, repaid_at=None, today=NEW + timedelta(days=5),
    )
    assert v["status"] == "не вернули"
