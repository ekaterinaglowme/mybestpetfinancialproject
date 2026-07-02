"""Слой данных БКИ-отчётов и внутренние флаги по займам клиента."""

import uuid
from datetime import date, datetime

import pytest

from bki_parse import BkiFeatures
from models import BkiReport
from repository import (
    create_loan,
    get_or_create_user,
    get_user_loan_flags,
    save_application,
    save_bki_report,
)

FEATURES = BkiFeatures(
    score=702, n_contracts=3, has_writeoff=True, has_current_delinquency=True,
    overdue_amount_kop=434900, max_dpd=6, n_late=2, debt_load_kop=14547100,
    inq_30=1, inq_90=3, inq_365=6,
)


async def _make_application(session, *, phone="+79991112233", amount=None, status="approved"):
    user = await get_or_create_user(
        session, last_name="Тестов", first_name="Тест", middle_name="",
        birth_date=date(1990, 1, 1), phone=phone,
    )
    application_id = uuid.uuid4()
    await save_application(
        session, application_id=application_id, user=user, amount=amount,
        country=None, status=status, reasons=[], received_at=datetime(2026, 7, 2, 12, 0),
    )
    return user, application_id


@pytest.mark.asyncio
async def test_save_bki_report_with_features(db_session):
    _, application_id = await _make_application(db_session)
    report = await save_bki_report(
        db_session, application_id=application_id,
        fetched_at=datetime(2026, 7, 2, 12, 0), status="ok",
        features=FEATURES, raw_xml="<xml/>",
    )
    stored = await db_session.get(BkiReport, application_id)
    assert stored is report
    assert stored.score == 702
    assert stored.has_current_delinquency is True
    assert stored.overdue_amount_kop == 434900
    assert stored.raw_xml == "<xml/>"


@pytest.mark.asyncio
async def test_save_bki_report_unavailable_all_features_null(db_session):
    _, application_id = await _make_application(db_session)
    await save_bki_report(
        db_session, application_id=application_id,
        fetched_at=datetime(2026, 7, 2, 12, 0), status="unavailable",
        features=None, raw_xml=None,
    )
    stored = await db_session.get(BkiReport, application_id)
    assert stored.status == "unavailable"
    assert stored.score is None
    assert stored.raw_xml is None


@pytest.mark.asyncio
async def test_loan_flags_empty_for_new_user(db_session):
    user, _ = await _make_application(db_session)
    assert await get_user_loan_flags(db_session, user.id) == (False, False)


@pytest.mark.asyncio
async def test_loan_flags_active_loan(db_session):
    user, application_id = await _make_application(db_session, amount=50000)
    await create_loan(
        db_session, application_id=application_id, amount=50000,
        issued_at=date(2026, 7, 1),
    )  # статус по умолчанию «выдано»
    assert await get_user_loan_flags(db_session, user.id) == (True, False)


@pytest.mark.asyncio
async def test_loan_flags_prior_default(db_session):
    user, application_id = await _make_application(db_session, amount=50000)
    loan = await create_loan(
        db_session, application_id=application_id, amount=50000,
        issued_at=date(2026, 6, 1),
    )
    loan.status = "не вернули"
    await db_session.flush()
    assert await get_user_loan_flags(db_session, user.id) == (False, True)


@pytest.mark.asyncio
async def test_loan_flags_returned_loan_is_clean(db_session):
    user, application_id = await _make_application(db_session, amount=50000)
    loan = await create_loan(
        db_session, application_id=application_id, amount=50000,
        issued_at=date(2026, 6, 1),
    )
    loan.status = "вернули"
    loan.repaid_at = date(2026, 6, 20)
    await db_session.flush()
    assert await get_user_loan_flags(db_session, user.id) == (False, False)
