# M09 安全代码执行沙箱实施计划

## Goal

在 M08 状态机基础上，把生产代码执行迁移到一次性受限容器，并保留 development/test 的本地 IPython 兼容能力；生产环境绝不回退到进程内执行。

## Phases

1. [x] 执行模型、限制策略和稳定错误契约
2. [x] 本地后端与旧 `CodeExecutor` 兼容门面
3. [x] 容器运行时协议与 `ContainerCodeExecutor`
4. [x] 配置选择和生产 fail-closed 边界
5. [x] Agent/任务执行路径接入统一执行接口
6. [x] 安全回归、审计元数据和文档
7. [x] 全量验证与 whole-branch review（人工静态复核完成；独立 reviewer 因服务端 429 未返回）

## Decisions

- 基线：M08 `a74afc2`。
- 生产 `APP_ENV=production` 强制 `container`；Docker 不可用、镜像缺失或容器失败都返回稳定错误，不回退 local。
- 测试默认使用 fake container runtime；真实 Docker 只作为显式环境集成测试。
- 原始代码、密钥、宿主绝对路径和未截断大输出不写入日志或审计记录。
- 旧 `CodeExecutor.execute_code()` 只作为 development/test 兼容入口。

## Errors Encountered

| Error | Attempt | Resolution |
| --- | --- | --- |
| M09 worktree initially based on main before M08 | 1 | Removed untouched worktree and recreated from `a74afc2` |
| Explicit unsafe backend could bypass production factory | 1 | Agent now requires `production_safe=True` for injected production backends |
| Invalid container figure path was returned to report flow | 1 | Failed `/output` path resolution now yields an empty path |
| New execution injection parameters broke the stable public `quick_analysis` signature | 1 | Keep settings forwarding internal and preserve the existing public parameter list |
| Whole-branch reviewer exhausted retries with HTTP 429 | 2 | Completed manual whole-branch static review and recorded the unavailable independent review in the Task 7 report |
| M08 orchestration wrapped legacy model/report failures and normalized public UUIDs | 1 | Keep orchestrator errors sanitized internally; restore legacy fallback, safe exception propagation, and raw UUIDs only at the compatibility facade |

## M10 分析结果与证据链（2026-09-24）

1. [x] 领域证据枚举、模型、错误码和 JSON 契约
2. [x] 指标登记、去重、复算和任务隔离
3. [x] 图表文件边界检查、事实/解释 claim 和报告数字校验
4. [x] 执行 ID、Agent 证据接入和报告提示词证据上下文
5. [x] 文档、M00-M09 全量回归和最终复核

## M10 Decisions

- 证据登记第一版使用任务级内存 `EvidenceRegistry`，不新增数据库迁移。
- 未验证指标不能进入报告数字上下文；无证据解释保留原文但标记为 `PENDING_CONFIRMATION`。
- 图表路径只允许位于当前任务输出根目录内；不存在、目录或越界路径不能生成报告链接。
- Agent 继续保留兼容结果字典，同时附加结构化指标、图表、claim 和 validation 快照。

## M11 报告生成服务（2026-09-24）

**状态：** 已完成（2026-09-26）

1. [x] 报告格式与核心模型
2. [x] Markdown、链接、数字和图片安全过滤
3. [x] 版本化模板与 HTML renderer
4. [x] ReportService 编排、原子写入和格式失败隔离
5. [x] Word renderer 适配和公共导出
6. [x] DataAnalysisAgent 接入与兼容结果
7. [x] 文档、全量回归和最终复核

## M11 Decisions

- 采用 `ReportService + ReportDocument`，canonical Markdown 作为 HTML/DOCX 的共同内容基准。
- 第一版实现 Markdown、HTML、DOCX；PDF 只保留未来 renderer 扩展点。
- 结构化指标和当前任务图表由系统注入，模型只提供叙述草稿。
- 各输出格式独立失败；Word 失败必须保留 Markdown，存储登记失败不删除本地产物。
- 保留 `DataAnalysisAgent`、`quick_analysis` 和 `utils/word_report_generator.py` 兼容入口。

## M11 Verification

- Task 1-6 的实现提交和独立复核均已完成；Task 6 的 legacy Word/storage seam 兼容修复包含在 `a07df38`。
- 最终全量验证：`E:\anaconda\python.exe -m pytest -q` 为 `804 passed, 3 skipped, 18 warnings in 59.01s`；报告/Agent/Word 相关回归为 `57 passed, 5 warnings`；README 精确公共导入示例离线执行成功；`E:\anaconda\python.exe -m compileall -q src` 和 `git diff --check` 均退出码 0。
- 3 个 skip 包含 2 个因当前环境未安装 async pytest 插件而跳过的 async 测试，以及 1 个当前环境不支持 symlink 的执行器测试；warnings 为既有 Pydantic、pytest async marker/coroutine 和 Python AST deprecation 警告。

## M11 Next Stage

- PDF 尚未实现；未来通过新增 renderer 接入，不计入 M11 完成范围。

## M11 Final-Review Repair (2026-09-26)

**状态：** 已完成（2026-09-26）

Authoritative findings: `sdd/final-review.md` (7 Important, 2 Minor). PDF remains out of scope.

1. [x] Reproduce findings and map existing interfaces/tests without changing `sdd/task-3-review.md`
2. [x] Add failing regression tests for numeric evidence, chart paths, escaping, storage URLs, cleanup, error redaction, output roots, HTML emphasis, and template versions
3. [x] Implement compatible fixes in ReportService, sanitizers/renderers, Agent wiring, and template validation
4. [x] Run focused and broader verification; inspect diff and compatibility seams
5. [x] Commit production/tests/docs changes after the independent final review

## M12 后台任务 Worker（2026-09-27）

**状态：** 已完成（2026-09-27）

### Goal

将长时间分析从同步调用拆为可持久化、可恢复、可取消的后台任务；生产环境使用 Celery + Redis，测试环境使用内存 fake broker，不依赖真实外部服务。

1. [x] 任务提交服务、幂等入队和 API DTO 契约
2. [x] Broker 抽象、Celery/Redis 适配和配置校验
3. [x] Worker 执行、状态事件、阶段计时和失败重试
4. [x] 取消、恢复、重复执行保护和执行器停止
5. [x] worker CLI、文档、迁移/配置和兼容性回归
6. [x] 全量验证、人工审查（本轮未创建 commit）

### M12 Decisions

- 数据库任务记录是执行事实来源；消息只携带 `task_id`，不携带用户输入、文件路径或密钥。
- 同一用户的相同幂等请求只创建并入队一次；重复提交直接返回原任务，不重复发送消息。
- Celery 使用 late acknowledgement、prefetch=1 和 worker-lost rejection；Redis URL 从 `Settings.redis_url` 读取。
- 重试只在 worker 明确判定为可重试时发生；耗尽后任务进入 `FAILED` 并持久化安全错误码/消息。
- 取消同时更新数据库、撤销 Celery 消息，并在活动 worker 中请求 orchestrator/执行器停止；任务不会在取消后启动新阶段。
- worker 重启恢复 stale `RUNNING` 任务为 `QUEUED`，追加恢复事件，然后重新入队；终态任务永不重复执行。

## M13 后端 API 收尾复核（2026-09-27）

**状态：** 已完成（2026-09-27）

### 目标

处理 M13 独立复核发现的队列崩溃窗口、开发环境消费者缺失和本地 Artifact 下载链路缺口；保留已有 API、Worker、数据库和存储兼容契约。

1. [x] 为 PENDING/QUEUED 入队崩溃窗口增加失败回归测试并定位恢复边界
2. [x] 修复 stale recovery 与幂等入队，验证重复消息不会重复执行
3. [x] 让 development 在配置 Redis 时可使用 Celery/Worker，并补齐启动文档
4. [x] 增加受保护的本地 Artifact HTTP 下载端点，校验权限、签名、过期、大小和哈希
5. [x] 运行聚焦回归、全量测试、编译、迁移和 diff 检查，更新本计划与进度

### M13 Verification

- 聚焦 Worker/API/配置回归：`59 passed`；安全补充回归：`22 passed`。
- 全量验证：`901 passed, 1 skipped`；唯一 skip 是当前环境不支持 symlink 的既有执行器测试。
- `compileall -q src`、editable install、重复 `alembic upgrade head`、Worker `--help` 和 `git diff --check` 均退出码 0。

### M12 Verification Notes

- Worker 聚焦测试覆盖提交幂等、入队失败、取消、原子认领、阶段事件、可重试失败、重试耗尽、stale 恢复、活动执行器停止和 Celery payload；全程使用 SQLite 与 fake broker。
- `Celery`/`Redis` 采用延迟导入；缺失 `REDIS_URL` 或 Worker extra 时在 Celery 启动边界给出明确错误，普通 API/执行器配置不被 Worker 依赖污染。
- 数据库迁移 `20260927_0004_worker_retry_transitions` 更新活动阶段重试的合法状态约束；消息永远只携带 `task_id`。
