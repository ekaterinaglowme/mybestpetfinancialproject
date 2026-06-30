"""Юнит-тесты бизнес-логики: расчёт возраста.

Чистая функция из server.py, без HTTP — гоняется мгновенно.
"""

from datetime import date

from server import calculate_age


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
