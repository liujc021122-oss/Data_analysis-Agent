"""initial persistence schema"""

from alembic import op
import sqlalchemy as sa

from data_analysis_agent.persistence.database import UTCDateTime, UUIDString

revision = "20260913_0001"
down_revision = None
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.create_table("users",
        sa.Column("user_id", UUIDString(), primary_key=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_table("datasets",
        sa.Column("dataset_id", UUIDString(), primary_key=True),
        sa.Column("user_id", UUIDString(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False), sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(255)), sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_datasets_user_id", "datasets", ["user_id"])
    op.create_table("analysis_tasks",
        sa.Column("task_id", UUIDString(), primary_key=True),
        sa.Column("user_id", UUIDString(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False), sa.Column("request_hash", sa.String(255), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", _enum("PENDING", "QUEUED", "RUNNING", "EXPLORING", "CLEANING", "ANALYZING", "VALIDATING", "REPORTING", "COMPLETED", "FAILED", "CANCELLED", name="ck_analysis_tasks_status"), nullable=False),
        sa.Column("max_rounds", sa.BigInteger(), nullable=False), sa.Column("created_at", UTCDateTime(), nullable=False), sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("error_code", sa.String(255)), sa.Column("error_message", sa.Text()), sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("model_call_count", sa.BigInteger(), nullable=False), sa.Column("model_duration_ms", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_analysis_tasks_user_idempotency"),
    )
    op.create_index("ix_analysis_tasks_status", "analysis_tasks", ["status"])
    op.create_index("ix_analysis_tasks_created_at", "analysis_tasks", ["created_at"])
    op.create_index("ix_analysis_tasks_status_created_at", "analysis_tasks", ["status", "created_at"])
    op.create_table("analysis_task_datasets",
        sa.Column("task_id", UUIDString(), sa.ForeignKey("analysis_tasks.task_id"), primary_key=True),
        sa.Column("dataset_id", UUIDString(), sa.ForeignKey("datasets.dataset_id"), primary_key=True),
    )
    op.create_index("ix_analysis_task_datasets_dataset_id", "analysis_task_datasets", ["dataset_id"])
    op.create_table("task_events",
        sa.Column("event_id", UUIDString(), primary_key=True), sa.Column("task_id", UUIDString(), sa.ForeignKey("analysis_tasks.task_id"), nullable=False),
        sa.Column("event_type", _enum("STATUS_CHANGED", "TOOL_CALLED", "EXECUTION_COMPLETED", "ARTIFACT_CREATED", "ERROR", name="ck_task_events_event_type"), nullable=False),
        sa.Column("from_status", _enum("PENDING", "QUEUED", "RUNNING", "EXPLORING", "CLEANING", "ANALYZING", "VALIDATING", "REPORTING", "COMPLETED", "FAILED", "CANCELLED", name="ck_task_events_from_status")),
        sa.Column("to_status", _enum("PENDING", "QUEUED", "RUNNING", "EXPLORING", "CLEANING", "ANALYZING", "VALIDATING", "REPORTING", "COMPLETED", "FAILED", "CANCELLED", name="ck_task_events_to_status"), nullable=False),
        sa.Column("message", sa.Text()), sa.Column("occurred_at", UTCDateTime(), nullable=False), sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_task_events_task_occurred", "task_events", ["task_id", "occurred_at"])
    op.create_table("tool_calls",
        sa.Column("tool_call_id", UUIDString(), primary_key=True), sa.Column("task_id", UUIDString(), sa.ForeignKey("analysis_tasks.task_id"), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False), sa.Column("arguments_json", sa.JSON(), nullable=False), sa.Column("result_json", sa.JSON()),
        sa.Column("status", _enum("PENDING", "RUNNING", "SUCCEEDED", "FAILED", name="ck_tool_calls_status"), nullable=False),
        sa.Column("started_at", UTCDateTime()), sa.Column("finished_at", UTCDateTime()), sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_tool_calls_task_id", "tool_calls", ["task_id"])
    op.create_table("executions",
        sa.Column("execution_result_id", UUIDString(), primary_key=True), sa.Column("tool_call_id", UUIDString(), sa.ForeignKey("tool_calls.tool_call_id")),
        sa.Column("success", sa.Boolean(), nullable=False), sa.Column("output_text", sa.Text(), nullable=False), sa.Column("error_text", sa.Text()),
        sa.Column("variables_json", sa.JSON(), nullable=False), sa.Column("duration_ms", sa.BigInteger()), sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_table("artifacts",
        sa.Column("artifact_id", UUIDString(), primary_key=True), sa.Column("task_id", UUIDString(), sa.ForeignKey("analysis_tasks.task_id"), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("file_path", sa.Text()),
        sa.Column("format", _enum("MARKDOWN", "DOCX", name="ck_artifacts_format")), sa.Column("mime_type", sa.String(255)), sa.Column("content_hash", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False), sa.Column("description", sa.Text()), sa.Column("source_tool_call_id", UUIDString(), sa.ForeignKey("tool_calls.tool_call_id")),
        sa.Column("metadata_json", sa.JSON(), nullable=False), sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])
    op.create_table("reports",
        sa.Column("report_id", UUIDString(), primary_key=True), sa.Column("artifact_id", UUIDString(), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("task_id", UUIDString(), sa.ForeignKey("analysis_tasks.task_id"), nullable=False), sa.Column("format", _enum("MARKDOWN", "DOCX", name="ck_reports_format"), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False), sa.Column("size_bytes", sa.BigInteger(), nullable=False), sa.Column("content_hash", sa.String(255)), sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("artifact_id", name="uq_reports_artifact_id"),
    )
    op.create_index("ix_reports_task_id", "reports", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_reports_task_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_artifacts_task_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("executions")
    op.drop_index("ix_tool_calls_task_id", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_task_events_task_occurred", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_analysis_task_datasets_dataset_id", table_name="analysis_task_datasets")
    op.drop_table("analysis_task_datasets")
    op.drop_index("ix_analysis_tasks_status_created_at", table_name="analysis_tasks")
    op.drop_index("ix_analysis_tasks_created_at", table_name="analysis_tasks")
    op.drop_index("ix_analysis_tasks_status", table_name="analysis_tasks")
    op.drop_table("analysis_tasks")
    op.drop_index("ix_datasets_user_id", table_name="datasets")
    op.drop_table("datasets")
    op.drop_table("users")
