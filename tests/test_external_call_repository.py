"""Слой данных журнала внешних вызовов external_service_calls."""

import uuid
from datetime import date, datetime

from models import ExternalServiceCall
from repository import get_or_create_user, save_application, save_external_call


async def _make_application(session, phone="+79990000000"):
    user = await get_or_create_user(
        session, last_name="Ж", first_name="Т", middle_name="",
        birth_date=date(1990, 1, 1), phone=phone,
    )
    application_id = uuid.uuid4()
    await save_application(
        session, application_id=application_id, user=user, amount=None,
        country=None, status="approved", reasons=[],
        received_at=datetime(2026, 7, 14, 12, 0),
    )
    return application_id


async def test_save_external_call_stores_payload(db_session):
    application_id = await _make_application(db_session)
    call_id = await save_external_call(
        db_session, service="bki", application_id=application_id,
        request={"passport": "0000024949"},
        response={"КодРезультата": "0", "Балл": "702"},
        status="ok", http_status=200, latency_ms=1043,
    )
    stored = await db_session.get(ExternalServiceCall, call_id)
    assert stored.service == "bki"
    assert stored.status == "ok"
    assert stored.http_status == 200
    assert stored.latency_ms == 1043
    assert stored.payload == {
        "request": {"passport": "0000024949"},
        "response": {"КодРезультата": "0", "Балл": "702"},
    }


async def test_save_external_call_appends_each_call(db_session):
    application_id = await _make_application(db_session)
    id1 = await save_external_call(
        db_session, service="stoplist", application_id=application_id,
        request={"passport": "1"}, response={"in_terror_list": False},
    )
    id2 = await save_external_call(
        db_session, service="stoplist", application_id=application_id,
        request={"passport": "1"}, response={"in_terror_list": False},
    )
    assert id1 != id2  # append-only: каждый вызов — своя строка


async def test_save_external_call_allows_null_application(db_session):
    call_id = await save_external_call(
        db_session, service="bki", application_id=None, request=None, response=None,
    )
    stored = await db_session.get(ExternalServiceCall, call_id)
    assert stored.application_id is None
    assert stored.payload == {"request": None, "response": None}
