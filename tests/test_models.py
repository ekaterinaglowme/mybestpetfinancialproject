import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models import Application, User


async def test_user_application_relationship(db_session):
    user = User(
        last_name="Иванов", first_name="Иван", middle_name="",
        birth_date=date(1990, 5, 1), phone="+79990000000",
    )
    db_session.add(user)
    await db_session.flush()

    app = Application(
        application_id=uuid.uuid4(), user=user, amount=100000,
        country="Россия", status="approved", reasons=[],
        received_at=datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(app)
    await db_session.flush()

    loaded = (await db_session.execute(select(Application))).scalar_one()
    assert loaded.user_id == user.id
    assert loaded.status == "approved"
    assert loaded.reasons == []


async def test_user_identity_unique(db_session):
    fields = dict(
        last_name="Петров", first_name="Пётр", middle_name="",
        birth_date=date(1995, 1, 1), phone="+79991112233",
    )
    db_session.add(User(**fields))
    await db_session.flush()
    db_session.add(User(**fields))
    with pytest.raises(IntegrityError):
        await db_session.flush()
