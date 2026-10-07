"""allow HTML report artifacts"""

from alembic import op
import sqlalchemy as sa


revision = "20261007_0008"
down_revision = "20261002_0007"
branch_labels = None
depends_on = None


_LEGACY_FORMATS = "format IN ('MARKDOWN', 'DOCX')"
_REPORT_FORMATS = "format IN ('MARKDOWN', 'HTML', 'DOCX')"


def _replace_format_constraint(table_name: str, expression: str) -> None:
    with op.batch_alter_table(table_name) as batch:
        batch.drop_constraint(f"ck_{table_name}_format", type_="check")
        batch.create_check_constraint(f"ck_{table_name}_format", expression)


def upgrade() -> None:
    _replace_format_constraint("artifacts", _REPORT_FORMATS)
    _replace_format_constraint("reports", _REPORT_FORMATS)


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in ("artifacts", "reports"):
        existing_html = connection.execute(
            sa.text(
                f"SELECT 1 FROM {table_name} "
                "WHERE format = 'HTML' LIMIT 1"
            )
        ).first()
        if existing_html is not None:
            raise RuntimeError(
                f"cannot downgrade while {table_name} contains HTML reports"
            )
    _replace_format_constraint("reports", _LEGACY_FORMATS)
    _replace_format_constraint("artifacts", _LEGACY_FORMATS)
