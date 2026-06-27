import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from models import Application, User
from repository import get_or_create_user, save_application

IDENTITY = dict(
    last_name="Сидоров", first_name="Семён", middle_name="",
    birth_date=date(1992, 3, 3), phone="+79995554433",
)


async def test_get_or_create_reuses_existing_user(db_session):
    first = await get_or_create_user(db_session, **IDENTITY)
    await db_session.flush()
    second = await get_or_create_user(db_session, **IDENTITY)
    assert first.id == second.id
    count = (await db_session.execute(select(func.count()).select_from(User))).scalar_one()
    assert count == 1


async def test_different_identity_creates_new_user(db_session):
    a = await get_or_create_user(db_session, **IDENTITY)
    b = await get_or_create_user(db_session, **{**IDENTITY, "phone": "+70000000000"})
    assert a.id != b.id


async def test_save_application_links_to_user(db_session):
    user = await get_or_create_user(db_session, **IDENTITY)
    await save_application(
        db_session, application_id=uuid.uuid4(), user=user, amount=50000,
        country="Россия", status="approved", reasons=[],
        received_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
    )
    app = (await db_session.execute(select(Application))).scalar_one()
    assert app.user_id == user.id
    assert app.status == "approved"
