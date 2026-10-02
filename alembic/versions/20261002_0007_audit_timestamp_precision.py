"""preserve fractional seconds for audit event ordering"""

from alembic import op
from sqlalchemy.dialects import mysql


revision = "20261002_0007"
down_revision = "20260929_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    op.alter_column(
        "audit_events",
        "occurred_at",
        existing_type=mysql.DATETIME(),
        type_=mysql.DATETIME(fsp=6),
        existing_nullable=False,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    op.alter_column(
        "audit_events",
        "occurred_at",
        existing_type=mysql.DATETIME(fsp=6),
        type_=mysql.DATETIME(),
        existing_nullable=False,
    )
