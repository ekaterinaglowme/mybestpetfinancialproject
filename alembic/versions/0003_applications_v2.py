"""applications: поля v2 + country nullable

Revision ID: 0003_applications_v2
Revises: 0002_loans
Create Date: 2026-06-27
"""
import sqlalchemy as sa

from alembic import op

revision = "0003_applications_v2"
down_revision = "0002_loans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("email", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("passport", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("region", sa.String(), nullable=True))
    op.add_column("applications", sa.Column("loan_purpose", sa.String(), nullable=True))
    op.alter_column(
        "applications", "country", existing_type=sa.String(), nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "applications", "country", existing_type=sa.String(), nullable=False,
    )
    op.drop_column("applications", "loan_purpose")
    op.drop_column("applications", "region")
    op.drop_column("applications", "passport")
    op.drop_column("applications", "email")
