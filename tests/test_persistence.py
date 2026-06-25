from datetime import date

from sqlalchemy import func, select

import db
from models import Application, User


def _payload(phone="+79991234567"):
    born = date.today().replace(year=date.today().year - 30)
    return {
        "last_name": "Иванов", "first_name": "Иван", "phone": phone,
        "country": "Россия", "birth_date": born.isoformat(), "amount": 100000,
    }


async def _count(model):
    async with db.AsyncSessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_application_persisted(async_client):
    resp = await async_client.post("/applications", json=_payload())
    assert resp.status_code == 200
    assert await _count(Application) == 1


async def test_repeat_same_identity_reuses_user(async_client):
    await async_client.post("/applications", json=_payload())
    await async_client.post("/applications", json=_payload())
    assert await _count(User) == 1
    assert await _count(Application) == 2


async def test_different_identity_creates_two_users(async_client):
    await async_client.post("/applications", json=_payload(phone="+79991234567"))
    await async_client.post("/applications", json=_payload(phone="+70000000000"))
    assert await _count(User) == 2


async def test_db_failure_returns_500(async_client):
    # Дропаем таблицы — следующий запрос к БД упадёт, ручка должна вернуть 500.
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
    resp = await async_client.post("/applications", json=_payload(phone="+79993334455"))
    assert resp.status_code == 500
