"""DROP bki_reports (−5.7 ГБ). Фичи берём из журнала external_service_calls (JSONB).

Витрина ОТМЕНЕНА: скоринг-фичи БКИ извлекаются напрямую из payload журнала
(`service='bki'`) — view/dataset `bki_features_live`. Отдельная таблица-копия не нужна.
`raw_xml` (~5.68 ГБ сырья) удаляется безвозвратно (парсер валидирован, требований хранить нет).

**Идемпотентна** (`DROP TABLE IF EXISTS`): если таблицу уже дропнули вручную на проде
(из-за нехватки места), миграция при накате станет no-op и просто продвинет версию Alembic —
ничего не упадёт.

Revision ID: 0007_drop_bki_reports
Revises: 0006_external_service_calls
Create Date: 2026-07-18
"""
import sqlalchemy as sa

from alembic import op

revision = "0007_drop_bki_reports"
down_revision = "0006_external_service_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS — таблицу могли уже дропнуть руками на проде (нехватка места).
    # DROP снимает и исходящий FK bki_reports → applications (задел под партиционирование).
    op.execute("DROP TABLE IF EXISTS bki_reports")


def downgrade() -> None:
    # Возврат только СТРУКТУРЫ (как в 0005). Данные, включая raw_xml, НЕ
    # восстанавливаются — цена возврата места (осознанно).
    op.create_table(
        "bki_reports",
        sa.Column(
            "application_id", sa.Uuid(),
            sa.ForeignKey("applications.application_id"), primary_key=True,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("n_contracts", sa.Integer(), nullable=True),
        sa.Column("has_writeoff", sa.Boolean(), nullable=True),
        sa.Column("has_current_delinquency", sa.Boolean(), nullable=True),
        sa.Column("overdue_amount_kop", sa.BigInteger(), nullable=True),
        sa.Column("max_dpd", sa.Integer(), nullable=True),
        sa.Column("n_late", sa.Integer(), nullable=True),
        sa.Column("debt_load_kop", sa.BigInteger(), nullable=True),
        sa.Column("inq_30", sa.Integer(), nullable=True),
        sa.Column("inq_90", sa.Integer(), nullable=True),
        sa.Column("inq_365", sa.Integer(), nullable=True),
        sa.Column("raw_xml", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('ok', 'no_history', 'unavailable')",
            name="ck_bki_report_status",
        ),
    )
