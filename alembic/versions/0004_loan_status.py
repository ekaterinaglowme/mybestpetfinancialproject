"""loans: статус займа для аналитики

Revision ID: 0004_loan_status
Revises: 0003_applications_v2
Create Date: 2026-06-30
"""
import sqlalchemy as sa

from alembic import op

revision = "0004_loan_status"
down_revision = "0003_applications_v2"
branch_labels = None
depends_on = None

_ALLOWED = "('выдано', 'вернули', 'не вернули', 'ошибка')"


def upgrade() -> None:
    # NOT NULL с server_default — существующие строки заполнятся «выдано».
    op.add_column(
        "loans",
        sa.Column("status", sa.String(), nullable=False, server_default="выдано"),
    )
    # Бэкфилл: уже погашенные займы → «вернули»; остальные остаются «выдано».
    op.execute("UPDATE loans SET status = 'вернули' WHERE repaid_at IS NOT NULL")
    op.create_check_constraint(
        "ck_loan_status", "loans", f"status IN {_ALLOWED}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_loan_status", "loans", type_="check")
    op.drop_column("loans", "status")
