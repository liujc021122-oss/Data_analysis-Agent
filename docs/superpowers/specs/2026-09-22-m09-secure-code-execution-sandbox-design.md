# M09 安全代码执行沙箱设计

## 目标

把不可信分析代码从生产进程内的 IPython 执行环境迁移到一次性、受限的独立 worker 容器；开发和测试继续保留本地执行兼容性，但本地执行器不能被生产配置选中，也不能在生产容器失败时作为回退。

## 威胁模型与安全不变量

执行代码视为不可信输入。M09 必须保证：

- 生产执行只能经过容器后端；Docker 不可用、容器创建失败或执行异常时，任务失败，不回退到进程内执行。
- 容器不以 privileged/root 身份运行，不挂载宿主源码、工作区、Docker socket 或宿主根目录；启用 `no-new-privileges`、默认 seccomp、丢弃全部 Linux capabilities、只读根文件系统。
- 输入文件以只读 bind mount 提供；输出目录是唯一可写 bind mount。挂载路径在宿主侧先做 `resolve()` 和范围校验，拒绝输出目录外的写入目标。
- 默认网络模式为 `none`。只有显式策略允许时才可启用网络；生产默认策略仍为禁止网络。
- 容器内通过 `pids-limit`、CPU quota、内存限制和执行超时限制资源；超时必须 kill 后 wait/remove，不能只依赖线程超时。
- 输出字节数、生成文件数量和单个文件路径均受限制；超限结果为失败并清理容器，不能把未限制的内容传回 Agent。
- 容器环境不继承宿主环境变量；只注入最小白名单变量，禁止 API key、数据库 URL、Redis URL 和 storage secret。
- 任务之间使用不同容器和临时工作目录，不共享 Python namespace、进程或未清理容器。

这些约束依赖 Docker daemon 本身受到可信运维控制；M09 不把 Docker daemon 暴露给分析代码，也不声称在恶意宿主/恶意 daemon 下提供隔离。

## 组件与接口

新增执行领域模型：

- `NetworkPolicy`: `DISABLED`、`ENABLED`，默认 `DISABLED`。
- `ExecutionLimits`: `timeout_seconds`、`memory_limit_bytes`、`cpu_limit`、`pids_limit`、`max_output_bytes`、`max_files`，全部有正数和上限校验。
- `ExecutionInput`: 逻辑文件名、来源路径和只读属性；不接受未经校验的任意宿主路径。
- `ExecutionRequest`: `task_id`、代码、输入文件、已解析输出目录、限制和网络策略。
- `ExecutionFile`: 输出文件的逻辑名、大小、SHA-256 和 MIME/后缀元数据。
- `ExecutionResult`: 成功标志、stdout/stderr、退出码、超时/资源限制标志、稳定错误码、代码 SHA-256、耗时和输出文件元数据。错误文本经过截断和路径/密钥清理。

执行端口：

```python
class CodeExecutionBackend(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
```

兼容门面保留：

```python
CodeExecutor.execute_code(code: str) -> dict[str, Any]
```

该门面只服务开发/测试兼容调用，由 `LocalCodeExecutor` 提供；它不能绕过 `ConfiguredCodeExecutor` 的生产选择。Agent 新路径使用统一 `ExecutionRequest`，兼容适配器负责把旧 executor double 映射到旧字典结果。

## 后端行为

### LocalCodeExecutor

复用现有 IPython 能力用于 `development`/`test`，增加统一结果模型、代码哈希、输出/文件限制和明确的非生产标记。它不是安全边界；生产配置校验禁止选择它。

### ContainerCodeExecutor

每次 `execute()`：

1. 校验 request、输入文件和输出目录范围，创建任务专用临时 staging 目录。
2. 将输入目录以只读方式挂载到固定容器路径，将输出目录以读写方式挂载到固定容器路径；代码通过 stdin 或受控临时脚本传入，不把宿主路径拼进代码上下文。
3. 使用 Docker CLI/适配 backend 创建一次性容器，固定非 root 用户、`--network none`（除非策略明确允许）、`--read-only`、tmpfs `/tmp`、`--cap-drop ALL`、`--security-opt no-new-privileges`、pids/CPU/memory 限制和无宿主环境继承。
4. 使用 monotonic deadline 等待；超时执行 kill、wait、remove。正常和异常路径都必须 remove，remove 失败写入安全审计错误而不泄露 daemon 详情。
5. 读取截断后的 stdout/stderr，扫描并校验输出文件数量、realpath、大小和哈希，生成 `ExecutionResult`。
6. 清理 staging 和容器句柄；清理失败不能留下可复用的任务进程。

Docker 调用通过可注入的 `ContainerRuntime` 协议隔离，测试使用 fake runtime，不要求本机安装 Docker，也不连接真实 daemon。

## 配置与选择

增加类型化设置：

- `EXECUTION_MODE`: `local` 或 `container`；默认 development/test 为 `local`，production 固定为 `container`。
- `EXECUTION_TIMEOUT_SECONDS`、`EXECUTION_MEMORY_LIMIT_BYTES`、`EXECUTION_CPU_LIMIT`、`EXECUTION_PIDS_LIMIT`、`EXECUTION_MAX_OUTPUT_BYTES`、`EXECUTION_MAX_FILES`。
- 可选 `EXECUTION_IMAGE`，生产缺失时给出明确配置错误；镜像名不来自模型输出。

配置选择在应用边界完成，而不是由模型、用户代码或请求参数决定。生产环境若 mode 为 local、Docker runtime 不可用、镜像缺失或容器启动失败，统一返回稳定 `EXECUTION_SANDBOX_UNAVAILABLE`/`EXECUTION_SANDBOX_FAILED` 错误。

## 错误与审计

定义稳定错误码：配置缺失、后端不可用、超时、内存/CPU/pids 限制、输出超限、文件数超限、路径越界、网络被拒绝、容器失败和清理失败。错误消息不得包含原始代码、API key、环境变量值、宿主绝对路径或完整 Docker 命令。

每次执行记录最小审计信息：`task_id`、代码 SHA-256、后端模式、开始/结束时间、耗时、退出码、资源/超时结果、输出文件元数据和稳定错误码。原始代码和大输出不进入日志或数据库。

## 测试与验收

测试不依赖真实模型 API、Docker daemon 或网络。核心测试覆盖：

- 无限循环达到 deadline 后被终止并清理 runtime；
- 内存、CPU、pids、输出大小和文件数量超限；
- 输出目录外路径、输入写入、宿主源码/环境变量读取、系统命令、子进程和网络访问被拒绝；
- 不同 task 使用独立 runtime/staging，不共享变量或残留进程；
- 正常图表/报告文件收集、代码哈希和安全元数据；
- production + local、Docker 缺失、容器启动失败均拒绝且不回退；
- development/test 仍可通过兼容 `CodeExecutor` 运行既有离线 contract/integration 测试。

真实 Docker 集成测试作为可选环境测试，不作为无 Docker CI 的必要条件；fake runtime 契约测试必须覆盖所有安全参数和清理调用。

## 非目标与迁移顺序

M09 不引入 Kubernetes、LangGraph、远程执行集群或新的对象存储协议，不重写 Agent 状态机。迁移顺序为：先稳定模型和 fake runtime 契约，再实现容器后端和配置工厂，最后接入 Agent 的代码执行路径并保留开发兼容门面。
