import pytest

import server

VALID = {
    "last_name": "Иванов", "first_name": "Иван", "phone": "+79991234567",
    "birth_date": "2000-05-15", "email": "ivan@example.ru",
    "passport": "1234567890", "region": "Москва",
    "loan_purpose": "покупка", "amount": 100000,
}


@pytest.fixture
def clean_blacklist(monkeypatch):
    async def fake(passport):
        return False
    monkeypatch.setattr(server, "check_passport", fake)


async def test_v2_approved(async_client, clean_blacklist):
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


async def test_v2_declined_in_black_list(async_client, monkeypatch):
    async def fake(passport):
        return True
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("чёрном списке" in r for r in body["reasons"])


async def test_v2_fail_closed_when_service_down(async_client, monkeypatch):
    async def fake(passport):
        raise server.BlackListError("down")
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "declined"
    assert any("Не удалось проверить" in r for r in body["reasons"])


async def test_v2_underage(async_client, clean_blacklist):
    resp = await async_client.post(
        "/applications/v2", json={**VALID, "birth_date": "2015-01-01"}
    )
    assert resp.json()["status"] == "declined"


async def test_v2_missing_passport_422(async_client, clean_blacklist):
    body = {k: v for k, v in VALID.items() if k != "passport"}
    resp = await async_client.post("/applications/v2", json=body)
    assert resp.status_code == 422


async def test_v2_loan_created_on_approval_with_amount(async_client, clean_blacklist):
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.json()["status"] == "approved"
    aid = resp.json()["application_id"]
    loan = await async_client.get(f"/loans/{aid}")
    assert loan.status_code == 200
    assert loan.json()["application_id"] == aid
    assert loan.json()["amount"] == 100000.0


async def test_v2_no_loan_when_declined(async_client, monkeypatch):
    async def fake(passport):
        return True
    monkeypatch.setattr(server, "check_passport", fake)
    resp = await async_client.post("/applications/v2", json=VALID)
    assert resp.json()["status"] == "declined"
    aid = resp.json()["application_id"]
    assert (await async_client.get(f"/loans/{aid}")).status_code == 404


async def test_v2_persisted(async_client, clean_blacklist):
    import db
    from sqlalchemy import select

    from models import Application

    await async_client.post("/applications/v2", json=VALID)
    async with db.AsyncSessionLocal() as s:
        row = (await s.execute(select(Application))).scalars().first()
    assert row is not None
    assert row.passport == "1234567890"
    assert row.email == "ivan@example.ru"
