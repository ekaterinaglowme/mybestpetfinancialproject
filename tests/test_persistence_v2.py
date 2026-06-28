import uuid
from datetime import datetime

from sqlalchemy import select

from models import Application
from repository import get_or_create_user, save_application


async def test_save_application_persists_v2_fields(db_session):
    user = await get_or_create_user(
        db_session, last_name="Иванов", first_name="Иван", middle_name="",
        birth_date=datetime(2000, 5, 15).date(), phone="+79991234567",
    )
    app_id = uuid.uuid4()
    await save_application(
        db_session, application_id=app_id, user=user, amount=100000,
        country=None, status="approved", reasons=[], received_at=datetime.now(),
        email="ivan@example.ru", passport="1234567890",
        region="Москва", loan_purpose="покупка",
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(Application).where(Application.application_id == app_id)
        )
    ).scalar_one()
    assert row.email == "ivan@example.ru"
    assert row.passport == "1234567890"
    assert row.region == "Москва"
    assert row.loan_purpose == "покупка"
    assert row.country is None
