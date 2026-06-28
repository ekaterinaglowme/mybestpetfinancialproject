"""Чистая логика займа: дисконтная ставка от времени и текущий долг.

Без БД и без обращения к «сегодня» — всё передаётся аргументами, поэтому легко
тестируется и переиспользуется в эндпоинтах (server.py).
"""

from datetime import date

# Дискретная ставка по возрасту займа: чем дольше — тем НИЖЕ % к телу займа.
# (порог_в_днях_включительно, ставка) по возрастанию; дальше — _RATE_BEYOND.
_RATE_TIERS: list[tuple[int, float]] = [
    (7, 0.10),    # 0–7 дней
    (30, 0.05),   # 8–30 дней
]
_RATE_BEYOND = 0.02  # > 30 дней


def rate_for_age(days: int) -> float:
    """Ставка к телу займа по числу прошедших дней."""
    for max_days, rate in _RATE_TIERS:
        if days <= max_days:
            return rate
    return _RATE_BEYOND


def amount_owed(amount, age_days: int) -> float:
    """Текущий долг = тело × (1 + ставка), округление до копеек.

    `amount` может прийти из БД как Decimal (Numeric) — приводим к float.
    """
    return round(float(amount) * (1 + rate_for_age(age_days)), 2)


def loan_view(*, application_id, amount, issued_at: date, repaid_at: date | None,
              today: date) -> dict:
    """Статус и долг для ответа API.

    Активный заём — возраст на `today`; возвращённый — заморожен на дату
    возврата (`repaid_at`).
    """
    as_of = repaid_at or today
    age_days = (as_of - issued_at).days
    return {
        "application_id": str(application_id),
        "amount": float(amount),
        "issued_at": issued_at,
        "status": "отдал" if repaid_at else "не отдал",
        "days_elapsed": age_days,
        "current_rate": rate_for_age(age_days),
        "amount_owed": amount_owed(amount, age_days),
        "repaid_at": repaid_at,
    }
