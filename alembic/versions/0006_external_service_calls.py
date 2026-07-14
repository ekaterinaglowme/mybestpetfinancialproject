"""external_service_calls: журнал вызовов внешних сервисов (ЧС, БКИ) в JSON

Revision ID: 0006_external_service_calls
Revises: 0005_bki_reports
Create Date: 2026-07-14
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_external_service_calls"
down_revision = "0005_bki_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_service_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("service", sa.String(), nullable=False),
        sa.Column(
            "application_id", sa.Uuid(),
            sa.ForeignKey("applications.application_id"), nullable=True,
        ),
        sa.Column(
            "called_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_esc_application_id", "external_service_calls", ["application_id"],
    )
    op.create_index(
        "ix_esc_service_called_at", "external_service_calls", ["service", "called_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_esc_service_called_at", table_name="external_service_calls")
    op.drop_index("ix_esc_application_id", table_name="external_service_calls")
    op.drop_table("external_service_calls")
