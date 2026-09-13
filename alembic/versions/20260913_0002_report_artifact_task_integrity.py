"""enforce report artifact task integrity"""

from alembic import op


revision = "20260913_0002"
down_revision = "20260913_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.create_unique_constraint(
            "uq_artifacts_artifact_task", ["artifact_id", "task_id"]
        )
    with op.batch_alter_table("reports") as batch_op:
        batch_op.create_foreign_key(
            "fk_reports_artifact_task",
            "artifacts",
            ["artifact_id", "task_id"],
            ["artifact_id", "task_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch_op:
        batch_op.drop_constraint("fk_reports_artifact_task", type_="foreignkey")
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_constraint("uq_artifacts_artifact_task", type_="unique")
