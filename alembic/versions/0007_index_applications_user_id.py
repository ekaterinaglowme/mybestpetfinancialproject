"""Индекс applications(user_id) под get_user_loan_flags.

Зачем: get_user_loan_flags ищет заявки клиента по user_id (+ join loans) — это
станет горячим путём при включении внутренней истории (has_active_loan /
has_prior_default) в make_decision_v2. FK в PostgreSQL сам индекс не создаёт →
без него seq scan по applications (~2.69 GiB).

На проде — CREATE INDEX CONCURRENTLY: обычный CREATE INDEX держит блокировку на
запись, а applications живая (поток заявок). CONCURRENTLY нельзя внутри транзакции,
поэтому — autocommit_block. На SQLite (тесты/CI без расширений) — обычный create_index.

Revision ID: 0007_index_applications_user_id
Revises: 0006_external_service_calls
Create Date: 2026-07-17
"""
from alembic import op

revision = "0007_index_applications_user_id"
down_revision = "0006_external_service_calls"
branch_labels = None
depends_on = None

INDEX = "ix_applications_user_id"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # IF NOT EXISTS — идемпотентность: CONCURRENTLY может оставить «невалидный»
        # индекс при сбое, повтор наката не должен падать на дубликате имени.
        with op.get_context().autocommit_block():
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
                "ON applications (user_id)"
            )
    else:
        op.create_index(INDEX, "applications", ["user_id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    else:
        op.drop_index(INDEX, table_name="applications")
