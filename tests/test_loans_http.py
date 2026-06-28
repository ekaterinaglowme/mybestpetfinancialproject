"""Интеграционные тесты ручек займа: выдача при одобрении, статус, возврат.

Ключ ручек — application_id (uuid из БД), а не персональные данные.
"""

import uuid
from datetime import date


def _approved_payload(amount=100000, phone="+79991234567"):
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов", "first_name": "Иван", "phone": phone,
        "country": "Россия", "birth_date": born.isoformat(), "amount": amount,
    }


async def _issue(async_client, **kw) -> str:
    resp = await async_client.post("/applications", json=_approved_payload(**kw))
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    return resp.json()["application_id"]


async def test_loan_created_on_approval_with_amount(async_client):
    aid = await _issue(async_client, amount=100000)
    resp = await async_client.get(f"/loans/{aid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["application_id"] == aid
    assert body["status"] == "не отдал"
    assert body["amount"] == 100000.0
    assert body["days_elapsed"] == 0          # выдан сегодня
    assert body["current_rate"] == 0.10
    assert body["amount_owed"] == 110000.0
    assert body["repaid_at"] is None


async def test_no_loan_when_approved_without_amount(async_client):
    born = date.today().replace(year=date.today().year - 30)
    resp = await async_client.post("/applications", json={
        "last_name": "Иванов", "first_name": "Иван", "phone": "+79991234567",
        "country": "Россия", "birth_date": born.isoformat(),
    })
    assert resp.status_code == 200
    aid = resp.json()["application_id"]
    assert (await async_client.get(f"/loans/{aid}")).status_code == 404


async def test_no_loan_for_declined(async_client):
    payload = _approved_payload()
    payload["country"] = "Китай"
    resp = await async_client.post("/applications", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "declined"
    aid = resp.json()["application_id"]
    assert (await async_client.get(f"/loans/{aid}")).status_code == 404


async def test_get_unknown_loan_404(async_client):
    assert (await async_client.get(f"/loans/{uuid.uuid4()}")).status_code == 404


async def test_repay_marks_repaid(async_client):
    aid = await _issue(async_client)
    resp = await async_client.post(f"/loans/{aid}/repay")
    assert resp.status_code == 200
    assert resp.json()["status"] == "отдал"
    assert resp.json()["repaid_at"] is not None
    assert (await async_client.get(f"/loans/{aid}")).json()["status"] == "отдал"


async def test_repay_twice_conflict(async_client):
    aid = await _issue(async_client)
    assert (await async_client.post(f"/loans/{aid}/repay")).status_code == 200
    assert (await async_client.post(f"/loans/{aid}/repay")).status_code == 409


async def test_repay_unknown_loan_404(async_client):
    assert (await async_client.post(f"/loans/{uuid.uuid4()}/repay")).status_code == 404
