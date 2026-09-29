"""add user credentials, sessions, and audit events"""

from alembic import op
import sqlalchemy as sa

from data_analysis_agent.persistence.database import UTCDateTime, UUIDString


revision = "20260929_0006"
down_revision = "20260927_0005"
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_normalized", sa.String(320), nullable=True))
        batch.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column(
                "role",
                sa.String(16),
                nullable=False,
                server_default="USER",
            )
        )
        batch.add_column(
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.create_check_constraint(
            "ck_users_role", "role IN ('USER', 'ADMIN')"
        )
    op.create_index(
        "ux_users_email_normalized",
        "users",
        ["email_normalized"],
        unique=True,
    )

    op.create_table(
        "auth_sessions",
        sa.Column("session_id", UUIDString(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            UUIDString(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("revoked_at", UTCDateTime()),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index(
        "ix_auth_sessions_user_expires",
        "auth_sessions",
        ["user_id", "expires_at"],
    )

    op.create_table(
        "audit_events",
        sa.Column("event_id", UUIDString(), primary_key=True),
        sa.Column("user_id", UUIDString(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "action",
            _enum(
                "REGISTERED",
                "LOGIN_SUCCEEDED",
                "LOGIN_FAILED",
                "LOGGED_OUT",
                "DATASET_UPLOADED",
                "DATASET_DELETED",
                "TASK_CREATED",
                "TASK_CANCELLED",
                "TASK_RETRIED",
                "ARTIFACT_DOWNLOAD_SUCCEEDED",
                "ARTIFACT_DOWNLOAD_DENIED",
                "AUTHENTICATION_DENIED",
                "AUTHORIZATION_DENIED",
                name="ck_audit_events_action",
            ),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(64)),
        sa.Column("target_id", UUIDString()),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("occurred_at", UTCDateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_audit_events_user_occurred",
        "audit_events",
        ["user_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_target",
        "audit_events",
        ["target_type", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_index("ix_audit_events_user_occurred", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_auth_sessions_user_expires", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ux_users_email_normalized", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_column("is_active")
        batch.drop_column("role")
        batch.drop_column("password_hash")
        batch.drop_column("email_normalized")
