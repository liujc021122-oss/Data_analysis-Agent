# M03 数据库持久化设计

## 状态

已由用户确认设计。本文档以 M02 的领域模型为基线，作为 M03 实施计划和验收测试的依据。

## 目标与范围

M03 为项目增加可安装、可测试、可迁移的数据库持久化层，使用户、数据集元数据、分析任务、任务事件、工具调用、代码执行结果、生成文件和报告在服务重启后仍可查询。

本阶段采用 SQLAlchemy 2 ORM、Alembic 和 MySQL 生产数据库。测试使用临时 SQLite 数据库，不需要 API Key、MySQL 服务、对象存储或真实模型 API。

本阶段不把 CSV、PNG、Markdown 或 DOCX 的内容写入数据库，也不重写现有分析算法。现有 `DataAnalysisAgent` 和 `quick_analysis` 保持兼容；持久化通过明确的数据库服务/依赖注入入口接入，未配置数据库时不改变原有离线运行方式。

## 既有代码边界

M02 已提供以下 Pydantic 领域模型，并继续作为业务层的唯一状态定义：

- `Dataset`
- `AnalysisTask`
- `TaskEvent`
- `ToolCall`
- `ExecutionResult`
- `MetricArtifact`、`ChartArtifact`、`ReportArtifact`
- `AgentState`
- `TaskStatus`、`TaskEventType`、`ToolCallStatus`、`ReportFormat`

M02 的 `persistence.models` 是 Pydantic 持久化记录，不直接承担 SQLAlchemy 映射职责。M03 新增 SQLAlchemy ORM 模型和 Repository；领域模型、Pydantic record、ORM 模型三者通过 mapper 转换，避免 SQLAlchemy Session 泄漏到 Agent、Worker 或 API DTO。

## 方案与技术约束

### 组件

```text
Settings.database_url
        |
        v
Database / Engine / SessionFactory
        |
        v
UnitOfWork  ---- owns transaction and session lifetime
        |
        +-- UserRepository
        +-- DatasetRepository
        +-- TaskRepository
        +-- TaskEventRepository
        +-- ToolCallRepository
        +-- ExecutionRepository
        +-- ArtifactRepository
        +-- ReportRepository
        |
        v
SQLAlchemy ORM models <-> Pydantic records <-> M02 domain models
```

- Repository 只负责查询、写入和 `flush`，不自行提交事务。
- Unit of Work 负责 Session 生命周期、提交和回滚。
- 领域状态转换仍调用 M02 的 `transition_task`；数据库只负责保存结果，不重新实现状态机。
- SQLAlchemy 使用 2.x typed declarative mapping；连接 URL 从 `DATABASE_URL` 读取。
- MySQL 使用生产连接；SQLite 仅用于测试和本地离线 schema 验证。
- UUID 通过一个跨 MySQL/SQLite 的 SQLAlchemy 类型保存，业务侧始终使用 `uuid.UUID`。
- JSON 字段保存已经由 M02 规范化的 JSON 值；时间在数据库中按 UTC 保存，映射回领域模型时恢复为带 UTC 时区的 `datetime`。

### 配置行为

`Settings` 增加数据库初始化所需的明确入口：

- `DATABASE_URL` 是数据库操作的连接地址；
- `Database.from_settings(settings)` 在需要持久化而 URL 缺失时抛出 `DatabaseConfigurationError`，错误信息明确指出 `DATABASE_URL`；
- `APP_ENV=test` 时测试必须显式传入临时 SQLite URL，不能读取生产配置；
- 生产环境的配置校验将把 `DATABASE_URL` 纳入必填项；
- 现有未启用持久化的兼容入口不因数据库未配置而在导入时失败。

输出目录和对象存储配置仍由 M01 的 `Settings` 管理。数据库只接收文件元数据，不负责创建或删除对象存储中的文件。

## 数据库 Schema

所有表使用 UTC 创建时间；主键使用 UUID。除特别说明外，JSON 字段均允许空对象，字符串列使用非空约束，外键使用显式索引。

### `users`

| 列 | 说明 |
| --- | --- |
| `user_id` | UUID 主键 |
| `external_id` | 可选的外部身份标识，非空时唯一 |
| `created_at` | UTC 创建时间 |
| `metadata_json` | 用户扩展元数据 |

### `datasets`

| 列 | 说明 |
| --- | --- |
| `dataset_id` | UUID 主键 |
| `user_id` | 上传者，外键到 `users.user_id` |
| `name` | 原始文件名/数据集名称 |
| `source_uri` | 对象存储地址；不保存文件内容 |
| `content_type` | MIME 类型 |
| `size_bytes` | 文件大小，非负 |
| `checksum` | 文件哈希 |
| `created_at` | UTC 创建时间 |
| `metadata_json` | 编码、列摘要等小型元数据 |

`source_uri` 是文件本体的外部地址，`name` 保存展示用文件名。CSV 内容不进入任何数据库列。

### `analysis_tasks`

| 列 | 说明 |
| --- | --- |
| `task_id` | UUID 主键 |
| `user_id` | 创建者，外键到 `users.user_id` |
| `idempotency_key` | 客户端幂等键，按用户唯一 |
| `request_hash` | 规范化请求的 SHA-256 指纹 |
| `query` | 分析请求 |
| `status` | `TaskStatus` 字符串值，数据库约束只允许 M02 定义的状态 |
| `max_rounds` | 最大分析轮数 |
| `model_call_count` | 模型调用总次数，默认 0 |
| `model_duration_ms` | 模型调用总耗时，默认 0 |
| `created_at` | UTC 创建时间 |
| `updated_at` | UTC 更新时间 |
| `error_code` | 失败错误码 |
| `error_message` | 失败信息 |
| `metadata_json` | 任务扩展元数据 |

创建唯一约束 `(user_id, idempotency_key)`；建立 `status`、`created_at` 和 `(status, created_at)` 索引。

### `analysis_task_datasets`

任务与数据集是多对多关系。`(task_id, dataset_id)` 为复合主键，两个列分别外键到任务和数据集，并建立反向查询索引。

### `task_events`

保存每一次阶段或状态变化：

`event_id`、`task_id`、`event_type`、`from_status`、`to_status`、`message`、`occurred_at`、`metadata_json`。

`event_type` 使用 M02 的 `TaskEventType` 值；`from_status` 可为空以表示初始 `PENDING` 事件；建立 `(task_id, occurred_at)` 索引。事件按 `occurred_at` 和 `event_id` 稳定排序返回。

### `tool_calls`

保存工具调用元数据：

`tool_call_id`、`task_id`、`tool_name`、`arguments_json`、`result_json`、`status`、`started_at`、`finished_at`、`error_message`。

工具调用结果只保存结构化结果或短文本，不把大型生成文件放进 JSON；文件统一通过 `artifacts` 的外部地址保存。

### `executions`

保存代码执行结果：

`execution_result_id`、可空的 `tool_call_id`、`success`、`output_text`、`error_text`、`variables_json`、`duration_ms`、`created_at`。

`duration_ms` 非负。执行输出属于诊断元数据，实施时限制为数据库适合的文本字段；大型输出由上层存储为文件并只在此处保存引用。

### `artifacts`

统一保存指标、图表和报告文件的元数据：

`artifact_id`、`task_id`、`artifact_type`、`name`、`file_path`、`size_bytes`、`content_hash`、`mime_type`、`format`、`description`、`source_tool_call_id`、`created_at`、`metadata_json`。

其中 `file_path` 保存本地受控路径或对象存储地址，取决于存储适配器；数据库不读取或写入文件本体。`size_bytes` 非负，哈希用于完整性校验。

### `reports`

报告是 artifact 的专用扩展：

`report_id`、`artifact_id`、`task_id`、`format`、`title`、`storage_uri`、`size_bytes`、`content_hash`、`created_at`、`metadata_json`。

`artifact_id` 唯一外键到 `artifacts.artifact_id`，保证一个报告扩展只对应一个 artifact。Markdown 和 DOCX 都只保存路径、格式、大小、哈希等元数据；Word 生成失败时由现有报告服务保留 Markdown 文件，持久化层记录 Markdown 的成功元数据，并把 DOCX 失败信息写入任务错误字段或错误事件，而不创建虚假的 DOCX 文件记录。

### 删除与完整性

默认不提供物理删除任务和数据集的业务接口。子记录保留任务审计历史；测试清理可使用数据库级联。用户、数据集和任务的外键默认限制删除，避免孤立审计记录。所有外键、唯一约束和索引均由 Alembic 迁移创建。

## Repository 与 Unit of Work 接口

实现中保留明确的类型化接口，核心签名如下；具体 ORM 查询隐藏在 Repository 内部：

```python
class UnitOfWork:
    users: UserRepository
    datasets: DatasetRepository
    tasks: TaskRepository
    task_events: TaskEventRepository
    tool_calls: ToolCallRepository
    executions: ExecutionRepository
    artifacts: ArtifactRepository
    reports: ReportRepository

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type, exc_value, traceback) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
```

`TaskRepository` 至少提供：

```python
get(task_id: UUID) -> AnalysisTask | None
get_for_update(task_id: UUID) -> AnalysisTask | None
create_idempotent(
    *, user_id: UUID, task: AnalysisTask,
    idempotency_key: str, request_hash: str,
) -> AnalysisTask
update(task: AnalysisTask) -> AnalysisTask
record_model_call(*, task_id: UUID, duration_ms: int) -> AnalysisTask
```

创建任务服务在同一 Unit of Work 中写入用户、任务、数据集关联和初始事件。状态更新流程先调用 `transition_task`，再调用 `tasks.update` 和 `task_events.append`，最后由 UoW 一起提交。

其他 Repository 至少支持 `add`、按主键 `get`、按任务查询和删除/清理所需的测试辅助操作，并返回 M02 领域模型或明确的 Pydantic record，不返回 ORM 实体。

## 幂等和并发行为

客户端在 `AnalysisTaskCreateRequest` 中提供非空 `idempotency_key`。请求指纹由 `query`、有序 `dataset_ids`、`max_rounds` 和规范化 `metadata` 生成，使用稳定 JSON 和 SHA-256；幂等键本身不参与指纹。

任务创建流程：

1. 按 `user_id + idempotency_key` 查询已有任务。
2. 若存在且 `request_hash` 相同，返回原任务，不新增任务或初始事件。
3. 若存在但指纹不同，抛出 `IdempotencyConflictError`。
4. 若不存在，在当前事务中插入任务、关联数据集和初始事件。
5. 并发请求遇到唯一键冲突时回滚当前保存点，重新查询唯一键对应的任务；指纹相同则返回该任务，不同则报告幂等冲突。

唯一约束是最终一致性兜底，Repository 不依赖应用进程内锁，因此服务重启或多进程部署仍保持幂等。

模型统计通过数据库表达式原子累加：

```sql
model_call_count = model_call_count + 1
model_duration_ms = model_duration_ms + :duration_ms
```

调用方传入负耗时时由 Pydantic/Repository 拒绝。单次统计更新与相应业务事件在需要时使用同一事务。

## 事务和错误处理

新增持久化错误类型：

- `DatabaseConfigurationError`：URL 缺失、格式无效或不支持；
- `EntityNotFoundError`：需要的实体不存在；
- `IdempotencyConflictError`：幂等键复用但请求指纹不同；
- `TransactionError`：提交、回滚或数据库连接失败；
- 既有 `PersistenceMappingError`：ORM/record/domain 转换失败。

UoW 的行为固定为：

- `with UnitOfWork(...)` 进入时创建一个 Session 和事务；
- 显式 `commit()` 成功后结束当前事务；
- 代码块抛出异常时自动 `rollback()`，然后关闭 Session；
- 提交期间的 SQLAlchemy 异常转换为 `TransactionError`，保留原始异常作为 cause；
- Repository 写入先 `flush`，因此外键、唯一约束和字段错误在业务事务内可被捕获；
- 失败事务不会留下任务、事件、关联关系或 artifact 的部分记录。

Mapper 在边界处验证状态、事件类型、报告格式和 UUID。数据库读取到未知枚举值时抛出 `PersistenceMappingError`，不静默降级为字符串。

## Alembic 迁移与初始化

新增：

```text
alembic.ini
alembic/
├── env.py
├── script.py.mako
└── versions/
    └── 20260913_0001_initial_schema.py
```

第一版迁移创建全部八类业务表和 `analysis_task_datasets` 关联表、外键、唯一约束、状态约束和索引。`alembic upgrade head` 是生产初始化和升级入口；重复执行不产生重复表或重复数据。`alembic downgrade -1` 删除第一版迁移创建的对象，供测试和开发回退。

数据库模块提供：

- `create_engine_from_settings(settings)`：校验 URL 并创建 Engine；
- `create_session_factory(engine)`：创建类型化 Session 工厂；
- `init_database(engine)`：供测试/本地初始化使用的 metadata 建表入口；生产部署仍通过 Alembic，避免绕过版本记录。

迁移环境从同一 ORM metadata 获取 `target_metadata`，并能离线编译 MySQL 方言。测试使用临时 SQLite 文件运行迁移两次，以验证可重复执行和服务重启后的数据读取。

## API 与运行时接入

`AnalysisTaskCreateRequest` 增加必填的非空 `idempotency_key`；其余 M02 请求字段保持不变。Repository 服务接收 `user_id`，不从全局变量或 API Key 推断用户。

持久化服务负责：

- 创建任务及初始 `PENDING` 事件；
- 状态变更时保存任务快照和状态事件；
- 保存工具调用、执行结果、artifact 和报告元数据；
- 记录模型调用总次数与总耗时；
- 查询任务详情及按时间排序的事件/产物。

现有 `quick_analysis` 的公开返回格式和无需数据库的调用方式保持兼容。未来接入 API/Worker 时通过注入 UoW 或持久化服务启用数据库，不把 SQLAlchemy 对象暴露给公共接口。

## 测试设计与验收映射

新增数据库测试分层如下：

1. 配置测试：缺少/无效 `DATABASE_URL` 给出 `DatabaseConfigurationError`；test 配置不读取 production dotenv。
2. Schema/迁移测试：临时 SQLite 初始化成功，Alembic upgrade 重复执行成功，MySQL 方言可编译；检查表、索引、唯一约束存在。
3. 模型边界测试：非法状态、非法字段类型、负文件大小/耗时、无效 UUID 和未知枚举值被清晰拒绝。
4. Repository CRUD 测试：创建、查询、更新用户、数据集和任务；追加并按时间读取事件；保存 tool call、execution、artifact、report 元数据。
5. 事务测试：在任务、关联数据集和初始事件写入中注入异常，确认整个事务回滚；状态更新与事件写入不能只成功一半。
6. 幂等测试：相同用户和 key 的相同请求返回同一 `task_id` 且只有一条任务/初始事件；不同请求指纹抛出 `IdempotencyConflictError`；唯一键竞争路径可重查。
7. 统计测试：多次记录模型调用后次数和耗时正确累加，负耗时被拒绝。
8. 重启模拟：关闭第一 Session，使用同一数据库 URL 创建第二 Engine/Session，仍可读取任务、事件和文件元数据。
9. 文件边界测试：数据库只保存 filename、size、hash、MIME 和 storage URI；测试数据库目录中不会出现 CSV/图片本体复制。
10. 回归测试：运行 M00/M01/M02 全量 no-key 测试，确认不调用真实模型 API、外部数据库或对象存储。

验收以测试输出、`compileall`、`pip check`、Alembic upgrade/downgrade 结果和 `git diff --check` 为证据；测试名称和断言按 `config`、`database`、`repositories`、`transactions`、`idempotency` 等模块分组，使失败信息能直接定位责任层。

## 非目标

- 不实现 MySQL 服务器、Redis 或对象存储服务；
- 不把二进制文件、完整 Markdown、完整执行输出或大模型 prompt 写入数据库；
- 不在本阶段重构 Agent 的分析循环；
- 不引入进程内全局缓存替代数据库一致性；
- 不改变 M00/M01/M02 已验证的公共接口，除为幂等创建增加明确字段和可选持久化接入外。
