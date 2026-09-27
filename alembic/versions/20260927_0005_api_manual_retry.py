"""allow a failed task to be queued for an explicit retry

Revision ID: 20260927_0005
Revises: 20260927_0004
"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_0005"
down_revision = "20260927_0004"
branch_labels = None
depends_on = None

_PREVIOUS = (
    "event_type != 'STATUS_CHANGED' OR "
    "(from_status IS NULL AND to_status = 'PENDING') OR "
    "(from_status = 'PENDING' AND to_status IN ('QUEUED', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'QUEUED' AND to_status IN ('RUNNING', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'RUNNING' AND to_status IN ('QUEUED', 'EXPLORING', 'CLEANING', 'ANALYZING', 'VALIDATING', 'REPORTING', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'EXPLORING' AND to_status IN ('QUEUED', 'CLEANING', 'ANALYZING', 'VALIDATING', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'CLEANING' AND to_status IN ('QUEUED', 'ANALYZING', 'VALIDATING', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'ANALYZING' AND to_status IN ('QUEUED', 'EXPLORING', 'CLEANING', 'VALIDATING', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'VALIDATING' AND to_status IN ('QUEUED', 'ANALYZING', 'REPORTING', 'FAILED', 'CANCELLED')) OR "
    "(from_status = 'REPORTING' AND to_status IN ('COMPLETED', 'FAILED', 'CANCELLED'))"
)
_RETRY = _PREVIOUS + " OR (from_status = 'FAILED' AND to_status = 'QUEUED')"


def _replace_constraint(expression: str) -> None:
    with op.batch_alter_table("task_events") as batch:
        batch.drop_constraint("ck_task_events_legal_status_transition", type_="check")
        batch.create_check_constraint("ck_task_events_legal_status_transition", expression)


def upgrade() -> None:
    _replace_constraint(_RETRY)


def downgrade() -> None:
    retry_event = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM task_events "
            "WHERE event_type = 'STATUS_CHANGED' "
            "AND from_status = 'FAILED' AND to_status = 'QUEUED' LIMIT 1"
        )
    ).first()
    if retry_event is not None:
        raise RuntimeError(
            "cannot downgrade while task_events contains FAILED -> QUEUED "
            "manual retry events"
        )
    _replace_constraint(_PREVIOUS)
