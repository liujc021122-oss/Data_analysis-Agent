"""persist task dataset request order"""

from alembic import op
import sqlalchemy as sa

from data_analysis_agent.persistence.database import UUIDString


revision = "20260913_0003"
down_revision = "20260913_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_task_datasets",
        sa.Column("position", sa.Integer(), nullable=True),
    )

    connection = op.get_bind()
    link = sa.table(
        "analysis_task_datasets",
        sa.column("task_id", UUIDString()),
        sa.column("dataset_id", UUIDString()),
        sa.column("position", sa.Integer()),
    )
    rows = connection.execute(
        sa.select(link.c.task_id, link.c.dataset_id).order_by(
            link.c.task_id, link.c.dataset_id
        )
    )
    next_positions: dict[object, int] = {}
    for task_id, dataset_id in rows:
        position = next_positions.get(task_id, 0)
        connection.execute(
            link.update()
            .where(
                sa.and_(
                    link.c.task_id == task_id,
                    link.c.dataset_id == dataset_id,
                )
            )
            .values(position=position)
        )
        next_positions[task_id] = position + 1

    with op.batch_alter_table("analysis_task_datasets") as batch_op:
        batch_op.alter_column(
            "position",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_analysis_task_datasets_task_position", ["task_id", "position"]
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_task_datasets") as batch_op:
        batch_op.drop_constraint(
            "uq_analysis_task_datasets_task_position", type_="unique"
        )
        batch_op.drop_column("position")
