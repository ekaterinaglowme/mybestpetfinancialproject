"""Тесты слоя данных по займам: создать и получить заём."""

import uuid
from datetime import date

from repository import create_loan, get_loan


async def test_create_and_get_loan(db_session):
    aid = uuid.uuid4()
    await create_loan(db_session, application_id=aid, amount=50000, issued_at=date(2026, 6, 1))
    loan = await get_loan(db_session, aid)
    assert loan is not None
    assert loan.application_id == aid
    assert float(loan.amount) == 50000.0
    assert loan.issued_at == date(2026, 6, 1)
    assert loan.repaid_at is None


async def test_get_unknown_loan_returns_none(db_session):
    assert await get_loan(db_session, uuid.uuid4()) is None
