"""Операции с БД: найти-или-создать пользователя и сохранить заявку."""

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from metrics import DB_WRITE_SECONDS
from models import Application, ExternalServiceCall, Loan, User


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


async def save_external_call(
    session: AsyncSession,
    *,
    service: str,
    application_id: uuid.UUID | None,
    request: dict | None,
    response: dict | None,
    status: str | None = None,
    http_status: int | None = None,
    latency_ms: int | None = None,
) -> int:
    """Записать один вызов внешнего сервиса в журнал (append-only). Возвращает id.

    `payload` собирается как {request, response}; весь ответ хранится в JSON —
    типизированных колонок под конкретный сервис нет.
    """
    call = ExternalServiceCall(
        service=service,
        application_id=application_id,
        status=status,
        http_status=http_status,
        latency_ms=latency_ms,
        payload={"request": request, "response": response},
    )
    session.add(call)
    with DB_WRITE_SECONDS.labels(operation="save_external_call").time():
        await session.flush()
    return call.id
