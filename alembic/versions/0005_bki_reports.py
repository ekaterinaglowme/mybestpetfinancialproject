"""bki_reports: ответ БКИ по заявке — статус, фичи, сырой XML

Revision ID: 0005_bki_reports
Revises: 0004_loan_status
Create Date: 2026-07-02
"""
import sqlalchemy as sa

from alembic import op

revision = "0005_bki_reports"
down_revision = "0004_loan_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("bki_reports")
