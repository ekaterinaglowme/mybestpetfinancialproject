"""ORM-модели: пользователь и его заявки (связь 1:N)."""

import uuid
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, ForeignKey, Index,
                        Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Date, DateTime, Uuid

from db import Base

# JSONB на Postgres, обычный JSON/TEXT на SQLite (для тестов).
ReasonsType = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "last_name", "first_name", "middle_name", "birth_date", "phone",
            name="uq_user_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    last_name: Mapped[str] = mapped_column(String)
    first_name: Mapped[str] = mapped_column(String)
    middle_name: Mapped[str] = mapped_column(String, default="", server_default="")
    birth_date: Mapped[date] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    applications: Mapped[list["Application"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        # Под get_user_loan_flags (поиск заявок клиента по user_id + join loans).
        # FK в PostgreSQL сам индекс не создаёт — заводим явно.
        Index("ix_applications_user_id", "user_id"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    reasons: Mapped[list[str]] = mapped_column(ReasonsType, default=list)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Поля заявки v2 (nullable — у заявок v1 остаются NULL).
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    passport: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    loan_purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="applications")


class Loan(Base):
    """Выданный заём: заводится при одобрении заявки с суммой. Ключ — application_id."""

    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('выдано', 'вернули', 'не вернули', 'ошибка')",
            name="ck_loan_status",
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.application_id"), primary_key=True
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    issued_at: Mapped[date] = mapped_column(Date)
    repaid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Статус для аналитики (хранится, не вычисляется): выдано → вернули / не вернули
    # / ошибка. Дефолт «выдано» проставляется при создании займа.
    status: Mapped[str] = mapped_column(
        String, default="выдано", server_default="выдано",
    )


class BkiReport(Base):
    """Ответ БКИ по заявке (1:1): статус похода, фичи, сырой XML.

    Сырой ответ бюро — юридический след и запас на переразбор при добавлении
    новых фич. Суммы в копейках, как отдаёт протокол бюро.
    """

    __tablename__ = "bki_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'no_history', 'unavailable')",
            name="ck_bki_report_status",
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.application_id"), primary_key=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_contracts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_writeoff: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_current_delinquency: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    overdue_amount_kop: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_dpd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_late: Mapped[int | None] = mapped_column(Integer, nullable=True)
    debt_load_kop: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inq_30: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inq_90: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inq_365: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_xml: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExternalServiceCall(Base):
    """Журнал вызовов внешних сервисов (ЧС, БКИ, будущие): запрос+ответ в JSON.

    Append-only: каждый вызов — отдельная строка. `payload` — весь запрос/ответ,
    приведённый к JSON (JSONB на Postgres, JSON на SQLite в тестах). Служебные поля
    вынесены колонками для быстрых выборок; само содержимое — в блобе.
    """

    __tablename__ = "external_service_calls"
    __table_args__ = (
        Index("ix_esc_application_id", "application_id"),
        Index("ix_esc_service_called_at", "service", "called_at"),
    )

    # BigInteger на Postgres; на SQLite (тесты) — Integer, иначе не автоинкрементит PK.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    service: Mapped[str] = mapped_column(String)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.application_id"), nullable=True
    )
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(ReasonsType, nullable=True)
