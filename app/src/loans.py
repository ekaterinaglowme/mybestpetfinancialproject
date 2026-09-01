"""Чистая логика займа: ставка от времени и текущий долг.

Без БД и без обращения к «сегодня» — всё передаётся аргументами, поэтому легко
тестируется и переиспользуется в эндпоинтах (server.py).

С 2026-09-01 ставка РАСТЁТ с возрастом займа (тянуть с возвратом невыгодно).
Займы, выданные раньше, продолжают жить по исторической «дисконтной» сетке:
условия уже выданного займа задним числом не меняются.
"""

from datetime import date

# Водораздел: займы, выданные с этой даты (включительно), считаются по новой сетке.
NEW_RATE_SINCE = date(2026, 9, 1)

# Новая сетка: чем дольше держат заём — тем ВЫШЕ % к телу.
# (порог_в_днях_включительно, ставка) по возрастанию; дальше — _RATE_BEYOND.
_RATE_TIERS: list[tuple[int, float]] = [
    (7, 0.20),    # 0–7 дней
    (30, 0.35),   # 8–30 дней
]
_RATE_BEYOND = 0.55  # > 30 дней

# Историческая «дисконтная» сетка (чем дольше — тем ниже) — только для займов,
# выданных ДО NEW_RATE_SINCE.
_RATE_TIERS_LEGACY: list[tuple[int, float]] = [
    (7, 0.10),    # 0–7 дней
    (30, 0.05),   # 8–30 дней
]
_RATE_BEYOND_LEGACY = 0.02  # > 30 дней


def rate_for_age(days: int, issued_at: date) -> float:
    """Ставка к телу займа: возраст в днях + дата выдачи (выбор сетки)."""
    if issued_at >= NEW_RATE_SINCE:
        tiers, beyond = _RATE_TIERS, _RATE_BEYOND
    else:
        tiers, beyond = _RATE_TIERS_LEGACY, _RATE_BEYOND_LEGACY
    for max_days, rate in tiers:
        if days <= max_days:
            return rate
    return beyond


def amount_owed(amount, age_days: int, issued_at: date) -> float:
    """Текущий долг = тело × (1 + ставка), округление до копеек.

    `amount` может прийти из БД как Decimal (Numeric) — приводим к float.
    """
    return round(float(amount) * (1 + rate_for_age(age_days, issued_at)), 2)


def loan_view(*, application_id, amount, status: str, issued_at: date,
              repaid_at: date | None, today: date) -> dict:
    """Представление займа для ответа API.

    `status` хранится в БД и передаётся как есть (loan_view его не вычисляет).
    Долг: активный заём считается на `today`; возвращённый — заморожен на дату
    возврата (`repaid_at`). Сетка ставки выбирается по дате выдачи.
    """
    as_of = repaid_at or today
    age_days = (as_of - issued_at).days
    return {
        "application_id": str(application_id),
        "amount": float(amount),
        "issued_at": issued_at,
        "status": status,
        "days_elapsed": age_days,
        "current_rate": rate_for_age(age_days, issued_at),
        "amount_owed": amount_owed(amount, age_days, issued_at),
        "repaid_at": repaid_at,
    }
