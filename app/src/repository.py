"""Операции с БД: найти-или-создать пользователя и сохранить заявку."""

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bki_parse import BkiFeatures
from metrics import DB_WRITE_SECONDS
from models import Application, BkiReport, Loan, User


async def get_or_create_user(
    session: AsyncSession,
    *,
    last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date,
    phone: str,
) -> User:
    """Вернуть пользователя по связке ФИО+ДР+телефон или создать нового."""
    stmt = select(User).where(
        User.last_name == last_name,
        User.first_name == first_name,
        User.middle_name == middle_name,
        User.birth_date == birth_date,
        User.phone == phone,
    )
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        last_name=last_name, first_name=first_name, middle_name=middle_name,
        birth_date=birth_date, phone=phone,
    )
    session.add(user)
    try:
        with DB_WRITE_SECONDS.labels(operation="get_or_create_user").time():
            await session.flush()
    except IntegrityError:
        # Параллельный запрос успел создать того же пользователя — берём существующего.
        await session.rollback()
        user = (await session.execute(stmt)).scalar_one()
    return user


async def save_application(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    user: User,
    amount: float | None,
    country: str | None,
    status: str,
    reasons: list[str],
    received_at: datetime,
    email: str | None = None,
    passport: str | None = None,
    region: str | None = None,
    loan_purpose: str | None = None,
) -> Application:
    """Создать заявку, привязанную к пользователю."""
    application = Application(
        application_id=application_id, user=user, amount=amount,
        country=country, status=status, reasons=reasons, received_at=received_at,
        email=email, passport=passport, region=region, loan_purpose=loan_purpose,
    )
    session.add(application)
    with DB_WRITE_SECONDS.labels(operation="save_application").time():
        await session.flush()
    return application


async def create_loan(
    session: AsyncSession, *, application_id: uuid.UUID, amount, issued_at: date,
) -> Loan:
    """Создать заём, привязанный к заявке (application_id)."""
    loan = Loan(application_id=application_id, amount=amount, issued_at=issued_at)
    session.add(loan)
    with DB_WRITE_SECONDS.labels(operation="create_loan").time():
        await session.flush()
    return loan


async def get_loan(session: AsyncSession, application_id: uuid.UUID) -> Loan | None:
    """Вернуть заём по application_id или None."""
    return await session.get(Loan, application_id)


async def save_bki_report(
    session: AsyncSession,
    *,
    application_id: uuid.UUID,
    fetched_at: datetime,
    status: str,
    features: BkiFeatures | None,
    raw_xml: str | None,
) -> BkiReport:
    """Сохранить итог похода в БКИ; при отсутствии фич (сбой/нет истории) — NULL."""
    feature_values = (
        dict(
            score=features.score,
            n_contracts=features.n_contracts,
            has_writeoff=features.has_writeoff,
            has_current_delinquency=features.has_current_delinquency,
            overdue_amount_kop=features.overdue_amount_kop,
            max_dpd=features.max_dpd,
            n_late=features.n_late,
            debt_load_kop=features.debt_load_kop,
            inq_30=features.inq_30,
            inq_90=features.inq_90,
            inq_365=features.inq_365,
        )
        if features is not None
        else {}
    )
    report = BkiReport(
        application_id=application_id, fetched_at=fetched_at, status=status,
        raw_xml=raw_xml, **feature_values,
    )
    session.add(report)
    with DB_WRITE_SECONDS.labels(operation="save_bki_report").time():
        await session.flush()
    return report


async def get_user_loan_flags(
    session: AsyncSession, user_id: uuid.UUID,
) -> tuple[bool, bool]:
    """(есть заём «выдано», есть заём «не вернули») по всем заявкам клиента."""
    stmt = (
        select(Loan.status)
        .join(Application, Loan.application_id == Application.application_id)
        .where(
            Application.user_id == user_id,
            Loan.status.in_(("выдано", "не вернули")),
        )
        .distinct()
    )
    statuses = set((await session.execute(stmt)).scalars())
    return "выдано" in statuses, "не вернули" in statuses
