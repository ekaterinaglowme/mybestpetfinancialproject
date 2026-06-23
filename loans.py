"""Учёт выданных займов: хранилище, дисконтная ставка от времени, статус.

Хранилище — dict в памяти процесса (теряется при рестарте; персистентность —
отдельная задача). Чистые функции принимают время аргументом, чтобы расчёты
были детерминированы и тестировались без подмены «сегодня».
"""

from dataclasses import dataclass
from datetime import date


class AlreadyRepaid(Exception):
    """Попытка отметить возврат у уже возвращённого займа."""


@dataclass
class Loan:
    loan_id: str
    amount: float
    issued_at: date
    repaid_at: date | None = None


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


def amount_owed(amount: float, age_days: int) -> float:
    """Текущий долг = тело × (1 + ставка(возраст)), округление до копеек."""
    return round(amount * (1 + rate_for_age(age_days)), 2)


# --- Хранилище (in-memory) -------------------------------------------------

_loans: dict[str, Loan] = {}


def reset() -> None:
    """Очистить хранилище (для изоляции тестов)."""
    _loans.clear()


def create_loan(loan_id: str, amount: float, issued_at: date) -> Loan:
    loan = Loan(loan_id=loan_id, amount=amount, issued_at=issued_at)
    _loans[loan_id] = loan
    return loan


def get_loan(loan_id: str) -> Loan | None:
    return _loans.get(loan_id)


def mark_repaid(loan_id: str, when: date) -> Loan:
    """Отметить возврат. KeyError — нет займа; AlreadyRepaid — уже возвращён."""
    loan = _loans[loan_id]              # KeyError, если займа нет
    if loan.repaid_at is not None:
        raise AlreadyRepaid(loan_id)
    loan.repaid_at = when
    return loan


# --- Представление статуса для API -----------------------------------------

def loan_view(loan: Loan, today: date) -> dict:
    """Статус и долг для ответа API.

    Активный заём — возраст на `today`; возвращённый — заморожен на дату
    возврата (`repaid_at`).
    """
    as_of = loan.repaid_at or today
    age_days = (as_of - loan.issued_at).days
    return {
        "loan_id": loan.loan_id,
        "amount": loan.amount,
        "issued_at": loan.issued_at,
        "status": "отдал" if loan.repaid_at else "не отдал",
        "days_elapsed": age_days,
        "current_rate": rate_for_age(age_days),
        "amount_owed": amount_owed(loan.amount, age_days),
        "repaid_at": loan.repaid_at,
    }
