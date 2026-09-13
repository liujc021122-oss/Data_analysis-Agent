# M03 数据库持久化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 M02 领域模型增加 SQLAlchemy 2 + Alembic 持久化层，使任务、数据集元数据、事件、调用、执行结果、artifact 和报告可在 MySQL 中可靠保存，并可用 SQLite 离线测试。

**Architecture:** 保留 M02 Pydantic 领域模型作为业务边界，新增 SQLAlchemy ORM 模型、record/ORM mapper、Unit of Work 和按职责划分的 Repository。Repository 只查询和 flush，Unit of Work 统一提交/回滚；任务创建服务用 `(user_id, idempotency_key)` 唯一约束和请求 SHA-256 指纹实现幂等，文件本体始终留在对象存储或受控文件存储中。

**Tech Stack:** Python 3.10+, Pydantic 2, SQLAlchemy 2.x, Alembic, PyMySQL, MySQL 生产数据库, SQLite 测试数据库, pytest。

## Global Constraints

- 生产数据库使用 MySQL；测试和本地离线 schema 验证使用临时 SQLite。
- 不调用真实模型 API，不要求 API Key、MySQL 服务或对象存储服务才能运行测试。
- `DATABASE_URL` 是数据库连接配置；缺失或格式无效时抛出明确的 `DatabaseConfigurationError`。
- 数据库只保存文件名、大小、哈希、MIME 类型和存储地址等元数据，不保存 CSV、PNG、Markdown 或 DOCX 本体。
- `TaskStatus`、`TaskEventType`、`ToolCallStatus` 和 `ReportFormat` 继续复用 M02 定义；状态迁移继续调用 `transition_task`。
- Repository 不提交事务；Unit of Work 负责 Session 生命周期、提交和回滚。
- `(user_id, idempotency_key)` 必须唯一；相同 key 的相同请求返回同一任务，不同请求指纹抛出 `IdempotencyConflictError`。
- 生产初始化和升级使用 `alembic upgrade head`；`init_database(engine)` 只供测试和本地初始化。
- 保留根目录兼容入口和现有 `quick_analysis` 行为；数据库能力通过明确的服务/依赖注入入口启用。
- 每个任务完成后运行对应测试并提交一个小而完整的 commit；不把本地数据库、API Key、输出文件或临时文件加入 Git。

---

## 文件结构与职责

执行时从 M02 提交 `278ec95` 创建独立 worktree/分支 `codex/m03-database-persistence`。以下路径均相对于该 worktree 根目录。

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml`、`requirements.txt` | 生产依赖和可安装依赖声明 |
| `src/data_analysis_agent/config/settings.py` | 生产数据库 URL 必填校验 |
| `src/data_analysis_agent/persistence/errors.py` | 数据库配置、实体不存在、幂等冲突和事务错误 |
| `src/data_analysis_agent/persistence/database.py` | Engine、跨数据库 UUID/UTC 类型、Session 工厂和本地 schema 初始化 |
| `src/data_analysis_agent/persistence/orm_models.py` | SQLAlchemy typed declarative ORM 表定义和 metadata |
| `src/data_analysis_agent/persistence/models.py` | Pydantic persistence record，作为 ORM mapper 的稳定中间层 |
| `src/data_analysis_agent/persistence/mappers.py` | M02 domain ↔ Pydantic record 转换 |
| `src/data_analysis_agent/persistence/orm_mappers.py` | ORM ↔ Pydantic record 转换 |
| `src/data_analysis_agent/persistence/repositories.py` | 八类实体 Repository、任务关联和原子模型统计 |
| `src/data_analysis_agent/persistence/unit_of_work.py` | Session 生命周期及事务边界 |
| `src/data_analysis_agent/services/idempotency.py` | 请求规范化和 SHA-256 指纹 |
| `src/data_analysis_agent/services/persistence.py` | 创建任务、状态事件、模型统计和产物持久化编排 |
| `src/data_analysis_agent/api/schemas.py` | 创建请求中的必填 `idempotency_key` |
| `alembic.ini`、`alembic/env.py`、`alembic/versions/20260913_0001_initial_schema.py` | Alembic 配置、metadata 绑定和初始迁移 |
| `tests/database/` | 配置、schema、Repository、事务和幂等测试 |
| `tests/integration/test_database_lifecycle.py` | 重启恢复、文件元数据边界和全链路数据库测试 |
| `README.md` | MySQL/Alembic 初始化及 SQLite 测试说明 |

---

### Task 1: 数据库依赖、配置校验和 Engine 基础

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `src/data_analysis_agent/config/settings.py`
- Modify: `tests/config/test_settings_contract.py`
- Create: `src/data_analysis_agent/persistence/errors.py`
- Create: `src/data_analysis_agent/persistence/database.py`
- Create: `tests/database/__init__.py`
- Create: `tests/database/test_database_config.py`

**Interfaces:**
- Produces `Database.from_settings(settings) -> Database`.
- Produces `create_engine_from_settings(settings) -> sqlalchemy.engine.Engine`.
- Produces `create_session_factory(engine) -> sqlalchemy.orm.sessionmaker[sqlalchemy.orm.Session]`.
- Produces `init_database(engine) -> None`; this function initially operates on the shared `Base.metadata`, and Task 2 supplies the table definitions.
- Produces `DatabaseConfigurationError`, `EntityNotFoundError`, `IdempotencyConflictError` and `TransactionError`.

- [ ] **Step 1: Write failing dependency and configuration tests**

Add `tests/database/test_database_config.py` with the following contract:

```python
from pathlib import Path

import pytest

from data_analysis_agent.config.settings import load_settings
from data_analysis_agent.persistence.database import Database
from data_analysis_agent.persistence.errors import DatabaseConfigurationError


def test_database_factory_names_missing_database_url():
    settings = load_settings(
        app_env="test",
        environ={"APP_ENV": "test"},
        dotenv_dir=Path(".") / "nonexistent-test-config",
    )

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        Database.from_settings(settings)


def test_database_factory_accepts_sqlite_url(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'database.sqlite3'}",
        },
    )

    database = Database.from_settings(settings)

    assert database.engine.url.get_backend_name() == "sqlite"
    assert database.session_factory.kw["expire_on_commit"] is False


@pytest.mark.parametrize("database_url", ["not-a-url", "ftp://example.invalid/db"])
def test_database_factory_rejects_unsupported_database_url(database_url):
    settings = load_settings(
        app_env="test",
        environ={"APP_ENV": "test", "DATABASE_URL": database_url},
    )

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        Database.from_settings(settings)
```

Update the existing production settings tests so every valid production fixture includes a non-secret `DATABASE_URL`, and add an assertion that a production configuration without it names `DATABASE_URL` in the missing-field message.

- [ ] **Step 2: Run the focused tests and verify the expected RED state**

Run:

```powershell
python -m pytest tests/database/test_database_config.py tests/config/test_settings_contract.py -q
```

Expected: collection or assertion failures because the SQLAlchemy dependencies, database error type, and factory do not exist yet; no real API or database connection is attempted.

- [ ] **Step 3: Add the dependency declarations and error types**

Add these project dependencies to both the PEP 621 production list and the requirements list:

```text
SQLAlchemy>=2.0,<3.0
alembic>=1.13,<2.0
PyMySQL>=1.1,<2.0
```

Create `src/data_analysis_agent/persistence/errors.py`:

```python
class PersistenceError(Exception):
    """Base class for database persistence failures."""


class DatabaseConfigurationError(PersistenceError, ValueError):
    """The configured database URL cannot be used."""


class EntityNotFoundError(PersistenceError):
    """A required persisted entity does not exist."""


class IdempotencyConflictError(PersistenceError):
    """An idempotency key was reused for a different request."""


class TransactionError(PersistenceError):
    """A database transaction failed."""
```

- [ ] **Step 4: Implement the shared Base, UTC/UUID types and factory**

Create `src/data_analysis_agent/persistence/database.py` with these stable pieces:

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import CHAR, DateTime, create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from ..config.settings import Settings
from .errors import DatabaseConfigurationError


class Base(DeclarativeBase):
    pass


class UUIDString(TypeDecorator[UUID]):
    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value if isinstance(value, UUID) else UUID(str(value)))

    def process_result_value(self, value, dialect):
        return UUID(value) if value is not None else None


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("database datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


@dataclass(frozen=True)
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        engine = create_engine_from_settings(settings)
        return cls(engine, create_session_factory(engine))


def create_engine_from_settings(settings: Settings) -> Engine:
    raw_url = settings.database_url
    if raw_url is None or not raw_url.strip():
        raise DatabaseConfigurationError("DATABASE_URL is required for persistence")
    try:
        url = make_url(raw_url)
        if url.get_backend_name() not in {"sqlite", "mysql"}:
            raise DatabaseConfigurationError(
                "DATABASE_URL must use sqlite or mysql backend"
            )
        return create_engine(url, future=True, pool_pre_ping=True)
    except DatabaseConfigurationError:
        raise
    except Exception as exc:
        raise DatabaseConfigurationError(f"Invalid DATABASE_URL: {raw_url}") from exc


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_database(engine: Engine) -> None:
    from . import orm_models  # noqa: F401  # register mapped tables before create_all

    Base.metadata.create_all(engine)
```

Use `mysql+pymysql://user:password@host:3306/data_analysis` or another SQLAlchemy MySQL URL in production. Do not call `engine.connect()` in the factory; connection failures belong to transaction/schema tests and must not make package import call an external service.

- [ ] **Step 5: Make production settings require the database URL without breaking development/test construction**

In `load_settings`, append `("DATABASE_URL", settings.database_url)` to the production-only missing list. Keep development and test URL optional at settings-load time so existing offline Agent construction still works; `Database.from_settings` is the explicit persistence boundary that raises when a URL is required.

- [ ] **Step 6: Run the focused tests and commit**

Run:

```powershell
python -m pytest tests/database/test_database_config.py tests/config/test_settings_contract.py -q
python -m pip check
```

Expected: all focused tests pass and `pip check` reports no broken requirements. Commit:

```powershell
git add pyproject.toml requirements.txt src/data_analysis_agent/config/settings.py src/data_analysis_agent/persistence/errors.py src/data_analysis_agent/persistence/database.py tests/database tests/config/test_settings_contract.py
git commit -m "feat: add database configuration and engine factory"
```

### Task 2: SQLAlchemy ORM Schema and Alembic Initial Migration

**Files:**
- Create: `src/data_analysis_agent/persistence/orm_models.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/20260913_0001_initial_schema.py`
- Create: `tests/database/test_schema.py`
- Modify: `src/data_analysis_agent/persistence/database.py`

**Interfaces:**
- Produces `Base.metadata` containing `users`, `datasets`, `analysis_tasks`, `analysis_task_datasets`, `task_events`, `tool_calls`, `executions`, `artifacts`, and `reports`.
- Produces ORM classes `UserORM`, `DatasetORM`, `AnalysisTaskORM`, `TaskEventORM`, `ToolCallORM`, `ExecutionORM`, `ArtifactORM`, and `ReportORM`.
- Produces the named indexes and unique constraint used by Repository tests and migrations.
- Alembic reads `DATABASE_URL` from its configured URL or environment and exposes the same metadata as `target_metadata`.

- [ ] **Step 1: Write the schema inspection tests**

Create `tests/database/test_schema.py` with tests that use `init_database` against a temporary SQLite file and inspect the result with `sqlalchemy.inspect`:

```python
EXPECTED_TABLES = {
    "users",
    "datasets",
    "analysis_tasks",
    "analysis_task_datasets",
    "task_events",
    "tool_calls",
    "executions",
    "artifacts",
    "reports",
}


def test_local_schema_has_all_tables_and_task_indexes(engine):
    init_database(engine)
    inspector = inspect(engine)

    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    index_names = {item["name"] for item in inspector.get_indexes("analysis_tasks")}
    assert {"ix_analysis_tasks_status", "ix_analysis_tasks_created_at"} <= index_names
    assert "ix_analysis_tasks_status_created_at" in index_names


def test_task_idempotency_is_unique_and_task_dataset_is_composite_key(engine):
    init_database(engine)
    inspector = inspect(engine)

    unique_names = {item["name"] for item in inspector.get_unique_constraints("analysis_tasks")}
    assert "uq_analysis_tasks_user_idempotency" in unique_names
    primary_key = inspector.get_pk_constraint("analysis_task_datasets")
    assert primary_key["constrained_columns"] == ["task_id", "dataset_id"]
```

Add a `tests/database/conftest.py` fixture that creates a SQLite file named `schema.sqlite3` under the directory supplied by pytest's `tmp_path` fixture and disposes the engine after each test. Keep the fixture file-only; it must not create files in the repository output directory.

- [ ] **Step 2: Run the schema tests to verify the expected RED state**

Run:

```powershell
python -m pytest tests/database/test_schema.py -q
```

Expected: import or table assertion failures because the ORM classes and Alembic files are not present.

- [ ] **Step 3: Define the ORM tables and cross-dialect constraints**

Create `orm_models.py` using SQLAlchemy `Mapped` annotations and `mapped_column` calls with the shared `Base`, `UUIDString`, and `UTCDateTime`. Use `JSON` for metadata/arguments/results/variables, `Text` for query and bounded diagnostic text, `BigInteger` for byte counts and duration totals, and `String` for names, hashes, statuses, and keys. The local import in `init_database` must register `orm_models` before calling `Base.metadata.create_all(engine)`; Alembic imports the module at module load for the same reason.

The table definitions must contain these exact constraints and indexes:

```python
Index("ix_analysis_tasks_status", AnalysisTaskORM.status)
Index("ix_analysis_tasks_created_at", AnalysisTaskORM.created_at)
Index(
    "ix_analysis_tasks_status_created_at",
    AnalysisTaskORM.status,
    AnalysisTaskORM.created_at,
)
UniqueConstraint(
    "user_id", "idempotency_key", name="uq_analysis_tasks_user_idempotency"
)
Index("ix_task_events_task_occurred", TaskEventORM.task_id, TaskEventORM.occurred_at)
Index("ix_analysis_task_datasets_dataset_id", task_dataset_link.c.dataset_id)
Index("ix_datasets_user_id", DatasetORM.user_id)
Index("ix_tool_calls_task_id", ToolCallORM.task_id)
Index("ix_artifacts_task_id", ArtifactORM.task_id)
Index("ix_reports_task_id", ReportORM.task_id)
```

Use a non-native SQLAlchemy enum helper for each M02 enum so SQLite and MySQL both get string values plus a check constraint:

```python
def enum_column(enum_type, constraint_name: str):
    return Enum(
        enum_type,
        name=constraint_name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )
```

`analysis_task_datasets` uses `(task_id, dataset_id)` as its primary key. `reports.artifact_id` is a unique foreign key to `artifacts.artifact_id`. Child rows reference their task with a foreign key; user/dataset/task business deletion is not exposed.

- [ ] **Step 4: Wire Alembic to the shared metadata**

Create `alembic.ini` with `script_location = alembic` and an empty `sqlalchemy.url`; `alembic/env.py` replaces it from the command config or `DATABASE_URL` before creating the engine. The important online configuration is:

```python
from alembic import context
from sqlalchemy import engine_from_config, pool

from data_analysis_agent.persistence.database import Base
from data_analysis_agent.persistence import orm_models  # noqa: F401

target_metadata = Base.metadata


def configured_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    configured = x_args.get("db_url", "").strip()
    if not configured:
        configured = context.config.get_main_option("sqlalchemy.url").strip()
    if not configured:
        configured = os.environ.get("DATABASE_URL", "").strip()
    if not configured:
        raise DatabaseConfigurationError(
            "DATABASE_URL is required to run Alembic migrations"
        )
    return configured
```

The full env module must support both `run_migrations_offline()` and `run_migrations_online()`, set `compare_type=True`, and use `pool.NullPool` online. It must never load an API client or call the LLM.

- [ ] **Step 5: Create and inspect the first migration**

Create revision `20260913_0001_initial_schema.py` with `revision = "20260913_0001"`, `down_revision = None`, an `upgrade()` that creates the nine tables in foreign-key order, then all indexes/constraints, and a `downgrade()` that drops them in reverse order. The migration must be explicit and deterministic; it must not call arbitrary application services.

Run:

```powershell
python -m alembic -x db_url="sqlite:///migration-check.sqlite3" upgrade head
python -m alembic -x db_url="sqlite:///migration-check.sqlite3" upgrade head
python -m alembic -x db_url="sqlite:///migration-check.sqlite3" downgrade base
Remove-Item -LiteralPath migration-check.sqlite3 -ErrorAction SilentlyContinue
```

`env.py` must give the `-x db_url` value precedence for this command. The first upgrade creates the schema, the second is a no-op, and downgrade removes it.

- [ ] **Step 6: Add Alembic repeatability and MySQL compilation tests**

Extend `test_schema.py` with a temporary SQLite migration test using `alembic.command.upgrade` twice and a table inspection after each call. Also add:

```python
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mysql


def test_initial_tables_compile_for_mysql():
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=mysql.dialect()))
        assert sql.startswith("\nCREATE TABLE")
```

The test must assert that the MySQL dialect can compile every table without opening a network connection.

- [ ] **Step 7: Run schema tests, compile checks and commit**

Run:

```powershell
python -m pytest tests/database/test_schema.py -q
python -m compileall -q src alembic
git diff --check
```

Expected: all schema/migration/MySQL compile tests pass. Commit:

```powershell
git add src/data_analysis_agent/persistence/database.py src/data_analysis_agent/persistence/orm_models.py alembic.ini alembic tests/database
git commit -m "feat: add SQLAlchemy schema and initial migration"
```

### Task 3: Persistence Records and Domain/ORM Mappers

**Files:**
- Modify: `src/data_analysis_agent/domain/models.py`
- Modify: `src/data_analysis_agent/persistence/models.py`
- Modify: `src/data_analysis_agent/persistence/mappers.py`
- Create: `src/data_analysis_agent/persistence/orm_mappers.py`
- Modify: `tests/domain/test_models.py`
- Modify: `tests/persistence/test_mappers.py`
- Create: `tests/database/test_sqlalchemy_mappers.py`

**Interfaces:**
- `AnalysisTask` gains default-zero `model_call_count` and `model_duration_ms` fields, both strict non-negative integers.
- `ChartArtifact` and `ReportArtifact` gain default-zero `size_bytes`; chart artifacts also gain optional `content_hash`.
- `AnalysisTaskRecord` carries owner/idempotency/request-hash and model-stat metadata without breaking existing record construction.
- Produces `UserRecord` and `ReportRecord`.
- Produces ORM/record mapping helpers for each SQLAlchemy entity; these helpers never return a live Session or ORM relationship.

- [ ] **Step 1: Write failing round-trip and strict-validation tests**

Add tests with these assertions:

```python
def test_analysis_task_model_statistics_are_strict_and_json_serializable():
    task = AnalysisTask(query="分析", model_call_count=2, model_duration_ms=35)

    assert task.model_call_count == 2
    assert task.model_duration_ms == 35
    assert json.loads(task.model_dump_json())["model_call_count"] == 2


@pytest.mark.parametrize("field", ["model_call_count", "model_duration_ms"])
def test_analysis_task_rejects_negative_model_statistics(field):
    with pytest.raises(ValidationError):
        AnalysisTask(query="分析", **{field: -1})


def test_report_record_and_artifact_record_are_json_serializable():
    report = ReportRecord(
        report_id=uuid4(), artifact_id=uuid4(), task_id=uuid4(),
        format="MARKDOWN", storage_uri="s3://bucket/report.md",
        size_bytes=12, content_hash="sha256:abc", created_at=now,
    )
    artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=report.task_id, artifact_type="REPORT",
        name="report.md", file_path="s3://bucket/report.md", size_bytes=12,
        content_hash="sha256:abc", created_at=now,
    )

    assert json.loads(report.model_dump_json())["size_bytes"] == 12
    assert json.loads(artifact.model_dump_json())["file_path"].startswith("s3://")
```

Add a test that maps every ORM row to a record and back for a task, event, tool call, execution, artifact, and report; assert enum values become M02 enum members only when converting back to a domain model.

- [ ] **Step 2: Run the mapper tests to verify the expected RED state**

Run:

```powershell
python -m pytest tests/domain/test_models.py tests/persistence/test_mappers.py tests/database/test_sqlalchemy_mappers.py -q
```

Expected: missing fields/classes or mapper import failures.

- [ ] **Step 3: Extend the domain and Pydantic persistence records**

Add these fields with defaults so existing M02 constructors remain valid:

```python
class AnalysisTask(DomainModel):
    # existing fields remain unchanged
    model_call_count: StrictInt = Field(default=0, ge=0)
    model_duration_ms: StrictInt = Field(default=0, ge=0)


class ChartArtifact(DomainModel):
    # existing fields remain unchanged
    size_bytes: StrictInt = Field(default=0, ge=0)
    content_hash: StrictStr | None = None


class ReportArtifact(DomainModel):
    # existing fields remain unchanged
    size_bytes: StrictInt = Field(default=0, ge=0)
```

Extend `AnalysisTaskRecord` with `user_id`, `idempotency_key`, `request_hash`, `model_call_count`, and `model_duration_ms`; extend `DatasetRecord` with `user_id`; extend `ArtifactRecord` with `task_id`, `size_bytes`, `description`, `source_tool_call_id`; add `UserRecord` and `ReportRecord`. Keep `extra="forbid"`, strict numeric types, UTC-aware timestamps, and JSON metadata fields.

- [ ] **Step 4: Update domain ↔ record mappers**

Update `task_to_record`/`record_to_task` and the artifact mappers to include the new fields. Keep `user_id`, idempotency, and request-hash values as persistence-only fields when a domain model does not own them. Use the existing `_normalize_json_value` so UUIDs, enums, datetimes, tuples and sets are JSON-safe. Unknown status/event/format values must raise `PersistenceMappingError` with the field name.

- [ ] **Step 5: Implement ORM ↔ record mappers**

Create `orm_mappers.py` with focused functions:

```python
def user_orm_to_record(row: UserORM) -> UserRecord:
    pass


def dataset_orm_to_record(row: DatasetORM) -> DatasetRecord:
    pass


def task_orm_to_record(row: AnalysisTaskORM, dataset_ids: list[UUID]) -> AnalysisTaskRecord:
    pass


def event_orm_to_record(row: TaskEventORM) -> TaskEventRecord:
    pass


def tool_call_orm_to_record(row: ToolCallORM) -> ToolCallRecord:
    pass


def execution_orm_to_record(row: ExecutionORM) -> ExecutionResultRecord:
    pass


def artifact_orm_to_record(row: ArtifactORM) -> ArtifactRecord:
    pass


def report_orm_to_record(row: ReportORM) -> ReportRecord:
    pass
```

Each inverse mapper must build a fresh ORM object, convert Pydantic enums to `.value`, copy JSON mappings to plain dictionaries, and convert all UUID-like values through `UUIDString`. It must not open files or infer metadata from a path.

- [ ] **Step 6: Run mapper and regression tests and commit**

Run:

```powershell
python -m pytest tests/domain/test_models.py tests/persistence/test_mappers.py tests/database/test_sqlalchemy_mappers.py -q
```

Expected: all existing domain/persistence mapper tests and new tests pass. Commit:

```powershell
git add src/data_analysis_agent/domain/models.py src/data_analysis_agent/persistence/models.py src/data_analysis_agent/persistence/mappers.py src/data_analysis_agent/persistence/orm_mappers.py tests/domain/test_models.py tests/persistence/test_mappers.py tests/database/test_sqlalchemy_mappers.py
git commit -m "feat: add persistence records and SQLAlchemy mappers"
```

### Task 4: Unit of Work and Core Repositories

**Files:**
- Create: `src/data_analysis_agent/persistence/unit_of_work.py`
- Create: `src/data_analysis_agent/persistence/repositories.py`
- Create: `tests/database/test_unit_of_work.py`
- Create: `tests/database/test_core_repositories.py`
- Modify: `tests/database/conftest.py`

**Interfaces:**
- `UnitOfWork(session_factory)` exposes `users`, `datasets`, `tasks`, `task_events`, `tool_calls`, `executions`, `artifacts`, and `reports` repositories.
- `UnitOfWork.__enter__()` returns itself; `commit()` commits; `rollback()` rolls back; `__exit__()` rolls back on an exception, commits an open normal transaction, and always closes the Session.
- `UserRepository.ensure(record: UserRecord) -> UserRecord`, `get(user_id: UUID) -> UserRecord | None`.
- `DatasetRepository.add(record: DatasetRecord) -> DatasetRecord`, `get(dataset_id: UUID) -> DatasetRecord | None`, `list_for_user(user_id: UUID) -> list[DatasetRecord]`.
- `TaskRepository.add(*, user_id: UUID, task: AnalysisTask, idempotency_key: str, request_hash: str) -> AnalysisTask`, `get(task_id: UUID) -> AnalysisTask | None`, `get_for_update(task_id: UUID) -> AnalysisTask | None`, `update(task: AnalysisTask) -> AnalysisTask`, and `attach_dataset(*, task_id: UUID, dataset_id: UUID) -> None`.
- `TaskEventRepository.append(event: TaskEvent) -> TaskEvent` and `list_for_task(task_id: UUID) -> list[TaskEvent]`.

- [ ] **Step 1: Add transaction and core Repository tests**

Create a session fixture using `Database.from_settings` with a SQLite file under pytest's `tmp_path`, call `init_database(engine)` once per test, and return a `uow_factory` that creates a fresh `UnitOfWork` per call.

Test the normal path:

```python
def test_core_repositories_create_query_update_and_attach(uow_factory):
    user_id, dataset_id, task_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    dataset = DatasetRecord(
        dataset_id=dataset_id, user_id=user_id, name="sample.csv",
        source_uri="s3://bucket/sample.csv", size_bytes=3,
        checksum="sha256:abc", created_at=now,
    )
    task = AnalysisTask(task_id=task_id, query="分析样例", dataset_ids=(dataset_id,))

    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=user_id, created_at=now))
        uow.datasets.add(dataset)
        uow.tasks.add(
            user_id=user_id, task=task,
            idempotency_key="key-1", request_hash="hash-1",
        )
        uow.tasks.attach_dataset(task_id=task_id, dataset_id=dataset_id)
        uow.commit()

    with uow_factory() as uow:
        restored = uow.tasks.get(task_id)
        assert restored is not None
        assert restored.dataset_ids == (dataset_id,)
        assert uow.datasets.get(dataset_id).source_uri == "s3://bucket/sample.csv"
```

Test rollback by raising `RuntimeError` after adding user, dataset, task and event inside one `with uow_factory()` block, then assert a second UoW cannot find any of the four records. Test `get_for_update` returns the same domain snapshot on SQLite; the SQL uses `with_for_update()` on MySQL.

- [ ] **Step 2: Run focused Repository tests to verify the expected RED state**

Run:

```powershell
python -m pytest tests/database/test_unit_of_work.py tests/database/test_core_repositories.py -q
```

Expected: missing UoW/Repository classes or methods.

- [ ] **Step 3: Implement Unit of Work transaction semantics**

Implement `UnitOfWork` with a fresh Session per context. The central behavior is:

```python
def __exit__(self, exc_type, exc_value, traceback):
    try:
        if exc_type is not None:
            self.rollback()
        elif self.session.in_transaction():
            self.commit()
    finally:
        self.session.close()


def commit(self):
    try:
        self.session.commit()
    except SQLAlchemyError as exc:
        self.session.rollback()
        raise TransactionError("database transaction commit failed") from exc
```

`rollback()` must call Session rollback and convert SQLAlchemy failures to `TransactionError`. Repositories receive the same Session instance and never call `commit()`.

- [ ] **Step 4: Implement user, dataset, task and event repositories**

Use SQLAlchemy `select` queries and `session.flush()` after every insert/update. Task reads must load dataset IDs from `analysis_task_datasets` in deterministic insertion/query order and map the ORM row through `task_orm_to_record` and `record_to_task`. `update(task)` updates status, query, max rounds, timestamps, errors, metadata and model statistics but never silently changes the owner or idempotency key.

`attach_dataset` must verify both task and dataset exist in the current transaction; missing rows raise `EntityNotFoundError`. `TaskEventRepository.list_for_task` orders by `(occurred_at, event_id)` and maps enum fields at the boundary.

- [ ] **Step 5: Test field and transaction failure mapping**

Add tests for a missing task/dataset, a duplicate association, a malformed enum row inserted through `session.execute`, and a forced `IntegrityError`. Assert error types contain the entity/field name and that a failed UoW leaves no partial rows.

- [ ] **Step 6: Run core tests and commit**

Run:

```powershell
python -m pytest tests/database/test_unit_of_work.py tests/database/test_core_repositories.py -q
python -m pytest tests/domain tests/persistence -q
```

Expected: all core persistence and prior domain mapper tests pass. Commit:

```powershell
git add src/data_analysis_agent/persistence/unit_of_work.py src/data_analysis_agent/persistence/repositories.py tests/database
git commit -m "feat: add unit of work and core repositories"
```

### Task 5: Tool, Execution, Artifact and Report Repositories

**Files:**
- Modify: `src/data_analysis_agent/persistence/repositories.py`
- Modify: `src/data_analysis_agent/persistence/unit_of_work.py`
- Create: `tests/database/test_artifact_repositories.py`
- Create: `tests/database/test_file_metadata_boundary.py`

**Interfaces:**
- `ToolCallRepository.add(call: ToolCall) -> ToolCall`, `get(tool_call_id: UUID) -> ToolCall | None`, `list_for_task(task_id: UUID) -> list[ToolCall]`.
- `ExecutionRepository.add(execution: ExecutionResult, *, tool_call_id: UUID | None = None) -> ExecutionResult`, `list_for_tool_call(tool_call_id: UUID) -> list[ExecutionResult]`.
- `ArtifactRepository.add(record: ArtifactRecord) -> ArtifactRecord`, `get(artifact_id: UUID) -> ArtifactRecord | None`, `list_for_task(task_id: UUID) -> list[ArtifactRecord]`.
- `ReportRepository.add(record: ReportRecord) -> ReportRecord`, `get(report_id: UUID) -> ReportRecord | None`, `list_for_task(task_id: UUID) -> list[ReportRecord]`.

- [ ] **Step 1: Write tests for all ancillary records and the no-file-copy boundary**

Create tool call, execution, chart artifact, Markdown artifact and report records with a path that does not exist. Add them in one UoW and assert that all records round-trip. The file boundary test must assert the repository does not call `Path.read_bytes`, `open`, `shutil.copy`, or create the path; it only stores the supplied URI/path, size, hash, MIME type and format.

Use this representative assertion:

```python
def test_artifact_repository_persists_metadata_without_copying_file(uow_factory, tmp_path, monkeypatch):
    external_path = tmp_path / "not-created.png"
    artifact = ArtifactRecord(
        artifact_id=uuid4(), task_id=uuid4(), artifact_type="CHART",
        name="trend.png", file_path=str(external_path), size_bytes=2048,
        content_hash="sha256:chart", mime_type="image/png",
        created_at=now,
    )

    with uow_factory() as uow:
        uow.artifacts.add(artifact)
        uow.commit()

    assert not external_path.exists()
    with uow_factory() as uow:
        restored = uow.artifacts.get(artifact.artifact_id)
        assert restored.file_path == str(external_path)
        assert restored.size_bytes == 2048
        assert restored.content_hash == "sha256:chart"
```

- [ ] **Step 2: Run the ancillary tests to verify the expected RED state**

Run:

```powershell
python -m pytest tests/database/test_artifact_repositories.py tests/database/test_file_metadata_boundary.py -q
```

Expected: missing repository methods or record fields.

- [ ] **Step 3: Implement ancillary repository mappings and queries**

Map `ToolCall` enum values to strings and back; store arguments/results as plain JSON; preserve `started_at`, `finished_at` and error message. Map `ExecutionResult` to `output_text`, `error_text`, `variables_json`, `duration_ms`, and a persistence-created UTC `created_at`; reject negative durations before flush.

For artifact/report repositories, accept only Pydantic records, issue one ORM insert, flush, and return a fresh record. Do not inspect a local path, calculate a hash, upload a file, or create a directory. `ReportRepository.add` must first ensure its artifact exists and must respect the unique `artifact_id` foreign key.

- [ ] **Step 4: Verify JSON, enum, foreign-key and metadata behavior**

Add tests that an unknown `ToolCallStatus`/`ReportFormat` row raises `PersistenceMappingError`, a report with a missing artifact raises `EntityNotFoundError`, a duplicate report artifact raises a transaction error, and large-looking URIs remain strings rather than file contents.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
python -m pytest tests/database/test_artifact_repositories.py tests/database/test_file_metadata_boundary.py tests/database/test_core_repositories.py -q
```

Expected: all repository tests pass and no file appears under `outputs/`. Commit:

```powershell
git add src/data_analysis_agent/persistence/repositories.py src/data_analysis_agent/persistence/unit_of_work.py tests/database
git commit -m "feat: persist tools executions artifacts and reports"

### Task 6: Idempotent Task Lifecycle Service and API DTO

**Files:**
- Modify: `src/data_analysis_agent/api/schemas.py`
- Modify: `src/data_analysis_agent/api/__init__.py`
- Create: `src/data_analysis_agent/services/idempotency.py`
- Create: `src/data_analysis_agent/services/persistence.py`
- Modify: `src/data_analysis_agent/services/__init__.py`
- Modify: `src/data_analysis_agent/persistence/repositories.py`
- Create: `tests/database/test_idempotency.py`
- Create: `tests/database/test_task_lifecycle.py`
- Modify: `tests/api/test_schemas.py`

**Interfaces:**
- `compute_request_hash(request: AnalysisTaskCreateRequest) -> str` returns a lowercase SHA-256 hex digest.
- `TaskCreationResult(task: AnalysisTask, created: bool)` identifies whether the current call inserted the task.
- `TaskRepository.create_idempotent_with_result(*, user_id, task, idempotency_key, request_hash) -> TaskCreationResult` handles the normal and unique-conflict paths.
- `TaskRepository.create_idempotent(*, user_id: UUID, task: AnalysisTask, idempotency_key: str, request_hash: str) -> AnalysisTask` remains the convenience wrapper required by the design.
- `TaskPersistenceService(uow_factory)` exposes `create_task(*, user_id: UUID, request: AnalysisTaskCreateRequest) -> AnalysisTask`, `transition_task(*, task_id: UUID, target: TaskStatus, message: str | None = None) -> tuple[AnalysisTask, TaskEvent]`, and `record_model_call(*, task_id: UUID, duration_ms: int) -> AnalysisTask`.

- [ ] **Step 1: Extend the API contract and write RED tests**

Add `idempotency_key: StrictStr` to `AnalysisTaskCreateRequest` with the existing `_nonblank` validator. Update the existing valid request construction to include `idempotency_key="contract-key"`. Add:

```python
def test_create_request_requires_a_nonblank_idempotency_key():
    with pytest.raises(ValidationError, match="idempotency_key"):
        AnalysisTaskCreateRequest(query="分析")

    with pytest.raises(ValidationError, match="idempotency_key"):
        AnalysisTaskCreateRequest(query="分析", idempotency_key="  ")
```

Add idempotency tests that create two requests with the same key and same fields, then a third with the same key and a changed query; the first two must return one task and the third must raise `IdempotencyConflictError`.

- [ ] **Step 2: Run API/idempotency tests to verify the expected RED state**

Run:

```powershell
python -m pytest tests/api/test_schemas.py tests/database/test_idempotency.py tests/database/test_task_lifecycle.py -q
```

Expected: the new DTO validation and service/repository symbols are missing.

- [ ] **Step 3: Implement stable request hashing**

Create `services/idempotency.py`:

```python
import hashlib
import json

from ..api.schemas import AnalysisTaskCreateRequest


def compute_request_hash(request: AnalysisTaskCreateRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Do not include the idempotency key in the digest. Preserve the tuple order of `dataset_ids`; use the already canonicalized M02 metadata.

- [ ] **Step 4: Implement Repository idempotency with a savepoint**

Add:

```python
@dataclass(frozen=True)
class TaskCreationResult:
    task: AnalysisTask
    created: bool
```

`create_idempotent_with_result` first queries `(user_id, idempotency_key)`. For an existing row, compare `request_hash` and either return `TaskCreationResult(task, False)` or raise `IdempotencyConflictError`. For a new row, insert and flush inside `session.begin_nested()`. Catch `IntegrityError` only for the named idempotency unique constraint, roll back the savepoint, re-query the row, compare hashes, and return `created=False` or raise the conflict error. Do not catch unrelated foreign-key or type errors as successful idempotency.

The convenience `create_idempotent` calls the result method and returns `.task`.

- [ ] **Step 5: Implement the persistence service and initial event**

`TaskPersistenceService.create_task` must perform these operations in one UoW:

```python
request_hash = compute_request_hash(request)
task = AnalysisTask(
    query=request.query,
    dataset_ids=request.dataset_ids,
    max_rounds=request.max_rounds,
    metadata=request.metadata,
)
with self.uow_factory() as uow:
    uow.users.ensure(UserRecord(user_id=user_id, created_at=utc_now()))
    for dataset_id in request.dataset_ids:
        if uow.datasets.get(dataset_id) is None:
            raise EntityNotFoundError(f"dataset {dataset_id} not found")
    result = uow.tasks.create_idempotent_with_result(
        user_id=user_id,
        task=task,
        idempotency_key=request.idempotency_key,
        request_hash=request_hash,
    )
    if result.created:
        for dataset_id in request.dataset_ids:
            uow.tasks.attach_dataset(
                task_id=result.task.task_id,
                dataset_id=dataset_id,
            )
        uow.task_events.append(
            TaskEvent(
                task_id=result.task.task_id,
                event_type=TaskEventType.STATUS_CHANGED,
                from_status=None,
                to_status=TaskStatus.PENDING,
                message="task created",
            )
        )
    uow.commit()
    return result.task
```

The actual implementation must use the imported M02 `utc_now`, `TaskEventType`, and `TaskStatus`. It must never append an initial event for `created=False`.

- [ ] **Step 6: Implement legal state changes and atomic model statistics**

`transition_task` in the service loads `get_for_update`, calls M02 `transition_task(current, target, message=message)`, updates the task, appends the returned event, commits, and returns both domain values. An invalid transition propagates `InvalidStatusTransitionError`; no task or event update is committed.

Add `TaskRepository.record_model_call` with a SQL expression update:

```python
statement = (
    update(AnalysisTaskORM)
    .where(AnalysisTaskORM.task_id == task_id)
    .values(
        model_call_count=AnalysisTaskORM.model_call_count + 1,
        model_duration_ms=AnalysisTaskORM.model_duration_ms + duration_ms,
        updated_at=utc_now(),
    )
)
```

Reject `duration_ms < 0`, require exactly one updated row, flush, reload the row and return the domain task with accumulated counts. This prevents lost updates across workers.

- [ ] **Step 7: Test idempotency, rollback, event and statistics behavior**

Add tests for:

- same user/key/request returns the same `task_id` and exactly one task-dataset association and one initial event;
- same key/different query raises `IdempotencyConflictError` and leaves the original task unchanged;
- two different users may reuse the same key;
- a monkeypatched event append failure rolls back the task and initial event together;
- valid `PENDING -> QUEUED` stores the status event;
- invalid `PENDING -> COMPLETED` leaves status and event count unchanged;
- three model calls with durations `10`, `25`, and `5` result in count `3` and total `40` after reopening the UoW;
- negative duration raises `ValueError` and does not change totals;
- the unique-conflict branch re-queries and returns the winning task.

- [ ] **Step 8: Run API/lifecycle tests and commit**

Run:

```powershell
python -m pytest tests/api/test_schemas.py tests/database/test_idempotency.py tests/database/test_task_lifecycle.py -q
```

Expected: all API, idempotency, lifecycle, rollback and model-statistics tests pass. Commit:

```powershell
git add src/data_analysis_agent/api src/data_analysis_agent/services src/data_analysis_agent/persistence/repositories.py tests/api/test_schemas.py tests/database
git commit -m "feat: add idempotent task persistence service"
```

### Task 7: Package Exports, Documentation, Restart Integration and Final Regression

**Files:**
- Modify: `src/data_analysis_agent/persistence/__init__.py`
- Modify: `src/data_analysis_agent/services/__init__.py`
- Modify: `README.md`
- Create: `tests/integration/test_database_lifecycle.py`
- Modify: `tests/database/conftest.py`

**Interfaces:**
- Public imports expose `Database`, `UnitOfWork`, `TaskPersistenceService`, ORM base, records, repositories and persistence errors without importing SQLAlchemy models through the Agent module.
- README documents MySQL URL format, `python -m alembic upgrade head`, local SQLite testing and the no-file-body rule.

- [ ] **Step 1: Write the restart and end-to-end persistence tests**

Create `tests/integration/test_database_lifecycle.py` using a file-backed SQLite URL. Test this exact lifecycle:

```python
def test_task_and_metadata_survive_engine_and_session_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'persistent.sqlite3'}"
    first = Database.from_settings(settings_for(database_url))
    init_database(first.engine)
    service = TaskPersistenceService(lambda: UnitOfWork(first.session_factory))

    task = service.create_task(user_id=user_id, request=request)
    # Add a chart/report through a UoW, then close and dispose first.engine.
    first.engine.dispose()

    second = Database.from_settings(settings_for(database_url))
    with UnitOfWork(second.session_factory) as uow:
        restored = uow.tasks.get(task.task_id)
        events = uow.task_events.list_for_task(task.task_id)
        artifacts = uow.artifacts.list_for_task(task.task_id)

    assert restored.task_id == task.task_id
    assert events[0].to_status is TaskStatus.PENDING
    assert artifacts[0].file_path == "s3://bucket/chart.png"
```

Also assert the SQLite file exists only because it is the database and contains metadata rows, while the supplied CSV/chart paths are not created or copied.

- [ ] **Step 2: Run the integration tests to verify the expected RED state**

Run:

```powershell
python -m pytest tests/integration/test_database_lifecycle.py -q
```

Expected: missing public exports, integration fixture, or lifecycle wiring until this task is implemented.

- [ ] **Step 3: Export the stable persistence surface**

Update `persistence/__init__.py` to export `Base`, `Database`, `UnitOfWork`, all Repository classes, ORM model records only where useful to migration tooling, Pydantic records, mappers, and the four persistence errors. Update `services/__init__.py` to export `TaskPersistenceService` and `compute_request_hash`. Do not import `DataAnalysisAgent` from persistence modules and do not create a global Engine at import time.

- [ ] **Step 4: Document deployment and test commands**

Add a README section with:

```text
# Production configuration
APP_ENV=production
DATABASE_URL=mysql+pymysql://user:password@host:3306/data_analysis

# Create or upgrade production schema
python -m alembic upgrade head

# Offline tests use a temporary SQLite URL and do not call MySQL/LLM/object storage
python -m pytest tests/database tests/integration -q
```

Document that `datasets.source_uri`, `artifacts.file_path`, and `reports.storage_uri` are external addresses and that duplicate `(user_id, idempotency_key)` requests return the original task.

- [ ] **Step 5: Run all verification commands**

Run with no API key and no external service variables:

```powershell
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
python -m pytest -q
python -m compileall -q src alembic
python -m pip check
git diff --check
```

Expected: all M00/M01/M02 and M03 tests pass; compilation, dependency and diff checks exit with code 0. The test output must identify failures under `config`, `database`, `persistence`, `api`, or `integration` paths.

- [ ] **Step 6: Verify migration repeatability and MySQL schema compilation one final time**

Run against a temporary SQLite file, then remove that exact file:

```powershell
$migrationDatabase = Join-Path ([System.IO.Path]::GetTempPath()) "m03-migration-check.sqlite3"
python -m alembic -x db_url="sqlite:///$migrationDatabase" upgrade head
python -m alembic -x db_url="sqlite:///$migrationDatabase" upgrade head
python -m alembic -x db_url="sqlite:///$migrationDatabase" downgrade base
Remove-Item -LiteralPath $migrationDatabase -Force -ErrorAction SilentlyContinue
```

Expected: both upgrades and the downgrade exit 0, and no migration database remains in the repository.

- [ ] **Step 7: Review the final diff and commit**

Run:

```powershell
git status --short
git diff --stat HEAD~1..HEAD
git log --oneline -8
```

Confirm no API key, `.sqlite3` file, output report, CSV/image body, or generated cache is tracked. Commit the documentation/exports/integration changes:

```powershell
git add src/data_analysis_agent/persistence src/data_analysis_agent/services/__init__.py README.md tests/integration tests/database
git commit -m "test: verify database restart lifecycle and regression"
```

## Completion Checklist

- [ ] `pip install -e .` installs SQLAlchemy, Alembic and PyMySQL through the project metadata.
- [ ] `python -m alembic upgrade head` creates all eight required business tables plus `analysis_task_datasets` and can be run twice.
- [ ] MySQL DDL compiles without a live MySQL connection.
- [ ] User, dataset, task, event, tool call, execution, artifact and report Repository operations round-trip through SQLite.
- [ ] Unit of Work commits complete changes and rolls back every partial change after an exception.
- [ ] Task status updates call M02 state transition validation and append the event atomically.
- [ ] Model call count and duration use an atomic SQL increment and survive Session/engine recreation.
- [ ] Same user/key/request is idempotent; same key with a changed request raises a conflict; different users may reuse a key.
- [ ] Database rows contain file metadata and external storage addresses, never CSV/image/Markdown/DOCX contents.
- [ ] Full no-key regression, compile, dependency, migration and diff checks pass.
