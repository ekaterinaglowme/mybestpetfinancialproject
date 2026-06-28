"""loans: учёт выданных займов

Revision ID: 0002_loans
Revises: 0001_initial
Create Date: 2026-06-23
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_loans"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loans",
        sa.Column(
            "application_id", sa.Uuid(),
            sa.ForeignKey("applications.application_id"), primary_key=True,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("repaid_at", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("loans")
