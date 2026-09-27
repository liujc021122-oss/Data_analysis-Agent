# M13 后端 API 设计

## 目标

为前端提供正式、可测试、具备用户隔离能力的 FastAPI 业务接口。API 只负责编排请求、权限、领域服务和响应，不直接调用 LLM、IPython、Python 执行器或对象存储底层文件路径。

## 范围

本阶段实现：

- FastAPI 应用工厂和业务 Router；
- 数据集上传、查询、详情和删除；
- 分析任务创建、分页查询、详情、取消、重试和事件查询；
- 图表和报告 Artifact 查询与授权下载 URL；
- Pydantic 请求/响应 DTO；
- 可替换的身份提供器；
- request ID、统一错误响应和分页；
- SQLite/fake broker/内存存储下的离线 API 测试。

本阶段不实现：

- JWT/OIDC 服务本身；
- WebSocket/SSE 实时订阅；
- 前端页面；
- API 进程内执行分析代码；
- PDF 报告或新的存储后端。

## 架构

应用通过 `create_app(container=None)` 创建。`APIApplication` 依赖容器持有数据库、存储、数据集上传服务、任务持久化服务、任务提交服务、文件访问服务、Broker 和 `PrincipalProvider`。生产部署必须显式提供认证适配器；开发和测试环境可以使用 `X-User-ID` UUID 头的默认实现。

请求链路为：

```text
HTTP 请求
  -> request-id 中间件
  -> PrincipalProvider
  -> Router + Pydantic 校验
  -> 领域服务
  -> 数据库 / Storage / Broker
  -> DTO 响应
```

Router 不创建全局数据库连接，不解析用户请求体中的 owner 字段，也不直接读取 `source_uri`、本地路径或执行器。任务消息仍然只包含 `task_id`。

### 模块职责

- `api/app.py`：应用工厂、中间件、异常处理器和 OpenAPI 元信息。
- `api/application.py`：`APIApplication` 依赖容器和开发/测试默认装配。
- `api/auth.py`：`Principal`、`PrincipalProvider` 协议和 `X-User-ID` 实现。
- `api/errors.py`：领域异常到 HTTP 错误响应的映射。
- `api/pagination.py`：分页参数与分页响应 DTO。
- `api/routers/datasets.py`：数据集接口。
- `api/routers/tasks.py`：分析任务和事件接口。
- `api/routers/artifacts.py`：Artifact 元数据和下载接口。
- `services/persistence.py`：增加用户范围的任务读取、事件读取和任务重试持久化操作。
- `datasets/service.py`：增加数据集目录查询和删除的领域服务能力。
- `persistence/repositories.py`：增加带 owner 条件的分页查询和数据集删除能力。
- `worker/service.py`：增加失败任务的显式重试服务操作。

## API 契约

分页统一使用 `page: int = 1` 和 `page_size: int = 20`，`page >= 1`、`1 <= page_size <= 100`。列表响应为：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "has_next": false
}
```

接口行为：

| 方法和路径 | 状态码 | 行为 |
| --- | --- | --- |
| `POST /api/datasets` | `201` | 接收 multipart CSV，返回 dataset ID、Profile、大小和哈希 |
| `GET /api/datasets` | `200` | 返回当前用户的数据集摘要分页 |
| `GET /api/datasets/{dataset_id}` | `200` | 返回当前用户的数据集详情和 Profile |
| `DELETE /api/datasets/{dataset_id}` | `204` | 删除对象和元数据；只允许所属用户 |
| `POST /api/analysis-tasks` | `202` | 创建任务并入队，返回 task ID、状态和幂等结果 |
| `GET /api/analysis-tasks` | `200` | 返回当前用户任务分页，可按状态过滤 |
| `GET /api/analysis-tasks/{task_id}` | `200` | 返回状态、错误摘要和 Artifact 摘要 |
| `POST /api/analysis-tasks/{task_id}/cancel` | `200` | 幂等取消任务并通知 Broker/活动执行器 |
| `POST /api/analysis-tasks/{task_id}/retry` | `202` | 仅允许 `FAILED` 任务重新入队 |
| `GET /api/analysis-tasks/{task_id}/events` | `200` | 返回当前用户任务的事件分页 |
| `GET /api/artifacts/{artifact_id}` | `200` | 返回图表/报告元数据，不暴露存储 URI |
| `GET /api/artifacts/{artifact_id}/download` | `200` | 返回授权短期 `download_url` 和过期秒数 |

跨用户资源和不存在资源统一返回 `404`，避免泄露资源存在性。

## 身份和权限

```python
class Principal:
    user_id: UUID

class PrincipalProvider(Protocol):
    def current_principal(self, request: Request) -> Principal: ...
```

开发/测试默认提供器读取 `X-User-ID`，必须是合法 UUID；缺失或非法时返回 `401`. 生产 `APIApplication` 没有显式认证提供器时拒绝创建。

所有数据集、任务、事件和 Artifact 查询都使用当前用户条件。Artifact 下载调用现有 `FileAccessService.create_download_url()`；只返回临时 URL，不返回 `file_path`、`source_uri` 或本地绝对路径。

## 错误协议

错误响应统一为：

```json
{
  "code": "DATASET_NOT_FOUND",
  "message": "dataset is not available",
  "details": {},
  "request_id": "..."
}
```

映射规则：

- 请求校验失败：`422`；
- 缺失或非法身份：`401`；
- 资源不存在或无权访问：`404`；
- 幂等冲突或非法任务操作：`409`；
- 文件过大：`413`；
- 数据库、存储或 Broker 暂时不可用：`503`；
- 未预期异常：`500`，只返回稳定公共错误码。

`RequestIdMiddleware` 接受合法且长度受限的 `X-Request-ID`，否则生成 UUID；每个响应都返回 `X-Request-ID`，错误体也包含相同值。异常消息不得包含密钥、SQL、内部文件路径、对象 URI 或未脱敏供应商错误。

## 任务重试语义

显式重试只允许 `FAILED` 任务。持久化层在同一事务内锁定任务、验证状态、清空旧错误、转为 `QUEUED` 并追加状态事件；随后由 Broker 发送只含 task ID 的消息。重复重试请求不能产生第二条消息。`COMPLETED`、`CANCELLED`、`PENDING`、`QUEUED`、`RUNNING` 和活动阶段任务返回 `409`。

为支持这一接口，状态机和 Alembic 迁移增加 `FAILED -> QUEUED` 合法转换；Worker 的自动重试语义仍由 M12 的 `RUNNING -> QUEUED` 路径控制。

## 测试策略

新增 API 契约和集成测试，使用 SQLite、临时本地存储、内存 Broker 和 fake Principal/Agent。禁止真实 LLM、Redis、Celery、对象存储和 Python 执行器。

必须覆盖：

- 上传合法 CSV 返回 dataset ID、Profile 和 `201`；
- 文件过大、非法 CSV 和缺少身份的统一错误；
- 数据集分页、详情、删除和跨用户隔离；
- 创建分析返回 `202` 和 task ID；
- 相同用户幂等请求不重复入队；
- 任务列表、详情、状态过滤和事件查询；
- 取消和显式重试的状态约束；
- Artifact/报告元数据查询和授权下载 URL；
- request ID 透传/生成、OpenAPI 文档和错误字段稳定性。

## 验收映射

- 上传可得到 dataset ID：`datasets` Router + DatasetUploadService 测试。
- 创建分析可得到 task ID：`tasks` Router + TaskSubmissionService 测试。
- 查询可看到状态：任务列表/详情 DTO 和 owner 过滤测试。
- 可以取消任务：取消服务、Broker revoke 和 HTTP 端点测试。
- 可以查询报告和图表：Artifact 查询及下载 URL 测试。
- 错误格式统一：异常处理器和每个错误状态测试。
- 不依赖真实模型：所有 API 测试使用 fake LLM/Agent/Broker。
