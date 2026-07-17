"""bki_features: витрина скоринг-фич БКИ (все колонки bki_reports, кроме raw_xml)

Зачем: bki_reports весит 5.68 GiB, почти вся тяжесть — сырой XML (raw_xml).
Скоринг-фичи нужны аналитике возврата постоянно, а XML — нет. Витрина снимает
компактную копию фич, после её проверки на проде bki_reports можно удалить
(отдельная миграция) и вернуть ~5.7 GiB диска.

В bki_reports сейчас никто не пишет (save_bki_report не вызывается, сбор
завершён), поэтому разовая копия не отстанет от источника.

Revision ID: 0007_bki_features
Revises: 0006_external_service_calls
Create Date: 2026-07-16
"""
from alembic import op

revision = "0007_bki_features"
down_revision = "0006_external_service_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE TABLE AS SELECT наследует типы колонок; NOT NULL/дефолты витрине
    # не нужны — в неё не пишет приложение, только читает аналитика.
    op.execute(
        """
        CREATE TABLE bki_features AS
        SELECT
            application_id,
            fetched_at,
            status,
            score,
            n_contracts,
            has_writeoff,
            has_current_delinquency,
            overdue_amount_kop,
            max_dpd,
            n_late,
            debt_load_kop,
            inq_30,
            inq_90,
            inq_365
        FROM bki_reports
        """
    )
    op.execute("ALTER TABLE bki_features ADD PRIMARY KEY (application_id)")


def downgrade() -> None:
    op.execute("DROP TABLE bki_features")
