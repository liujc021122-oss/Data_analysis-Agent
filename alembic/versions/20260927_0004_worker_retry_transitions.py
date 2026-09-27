"""allow active analysis stages to be requeued for worker retries

Revision ID: 20260927_0004
Revises: 20260913_0003
"""

from alembic import op


revision = "20260927_0004"
down_revision = "20260913_0003"
branch_labels = None
depends_on = None


_LEGAL_TRANSITIONS = (
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


def upgrade() -> None:
    with op.batch_alter_table("task_events") as batch:
        batch.drop_constraint(
            "ck_task_events_legal_status_transition", type_="check"
        )
        batch.create_check_constraint(
            "ck_task_events_legal_status_transition", _LEGAL_TRANSITIONS
        )


def downgrade() -> None:
    # The downgrade restores the pre-worker constraint.  It is intentionally
    # explicit so Alembic can run the migration on SQLite and MySQL alike.
    legacy = _LEGAL_TRANSITIONS.replace(
        "(from_status = 'RUNNING' AND to_status IN ('QUEUED', ",
        "(from_status = 'RUNNING' AND to_status IN (",
    ).replace(
        "(from_status = 'EXPLORING' AND to_status IN ('QUEUED', ",
        "(from_status = 'EXPLORING' AND to_status IN (",
    ).replace(
        "(from_status = 'CLEANING' AND to_status IN ('QUEUED', ",
        "(from_status = 'CLEANING' AND to_status IN (",
    ).replace(
        "(from_status = 'ANALYZING' AND to_status IN ('QUEUED', ",
        "(from_status = 'ANALYZING' AND to_status IN (",
    ).replace(
        "(from_status = 'VALIDATING' AND to_status IN ('QUEUED', ",
        "(from_status = 'VALIDATING' AND to_status IN (",
    )
    with op.batch_alter_table("task_events") as batch:
        batch.drop_constraint(
            "ck_task_events_legal_status_transition", type_="check"
        )
        batch.create_check_constraint(
            "ck_task_events_legal_status_transition", legacy
        )
