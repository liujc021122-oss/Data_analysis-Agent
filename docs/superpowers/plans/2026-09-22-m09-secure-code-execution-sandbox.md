# M09 安全代码执行沙箱实施计划

## Goal

在 M08 状态机基础上，把生产代码执行迁移到一次性受限容器，并保留 development/test 的本地 IPython 兼容能力；生产环境绝不回退到进程内执行。

## Global constraints

- 生产执行只能经过容器后端；Docker/runtime 不可用、镜像缺失、容器创建失败或容器执行失败时，任务失败，不回退 local。
- 默认网络为 disabled；输入只读挂载，输出是唯一可写挂载；不挂载宿主源码、工作区、Docker socket 或宿主根目录。
- 容器使用非 root、`no-new-privileges`、只读根文件系统、丢弃 capabilities，并设置 CPU、内存、pids、超时、输出字节数和文件数上限。
- 原始代码、宿主绝对路径、环境变量值、API key、完整 Docker 命令和未截断的大输出不得进入日志、异常或审计元数据。
- 测试不依赖真实模型 API、Docker daemon 或网络；fake runtime 覆盖容器参数和清理协议。
- `CodeExecutor.execute_code()` 只保留为 development/test 兼容入口；Agent 新路径使用统一执行请求。

## Tasks

### 1. 执行模型、限制策略和稳定错误契约

- 先为 `NetworkPolicy`、`ExecutionLimits`、`ExecutionInput`、`ExecutionRequest`、`ExecutionFile`、`ExecutionResult` 和执行审计字段写失败测试。
- 增加路径范围校验、逻辑文件名校验、正数/上限校验、代码 SHA-256 和安全文本截断/清理。
- 定义 `CodeExecutionBackend` 协议与稳定错误码/异常；错误不得泄露原始代码或宿主路径。
- 验证：新增执行领域单元测试先 RED，再 GREEN；现有 M00-M08 测试无回归。

### 2. 本地后端与旧 `CodeExecutor` 兼容门面

- 为 development/test 实现 `LocalCodeExecutor`，复用既有 IPython 能力但返回统一 `ExecutionResult`，增加超时/输出/文件限制和代码哈希。
- 将原 `CodeExecutor` 保留为兼容门面，确保旧 `execute_code()`、`set_variable()` 和图表流程仍可用。
- 明确本地后端不是安全边界，并测试任务之间 reset/隔离以及失败结果。
- 验证：本地执行 contract、图表/报告集成测试及新增后端测试。

### 3. 容器运行时协议与 `ContainerCodeExecutor`

- 定义可注入 `ContainerRuntime`，实现 Docker CLI 适配或等价运行时封装；测试使用 fake runtime。
- 每次请求创建独立 staging/容器，固定 `/input` 只读、`/output` 可写，代码通过 stdin/受控脚本传入。
- 传递 `network=none`、非 root、read-only rootfs、tmpfs、cap-drop、no-new-privileges、pids/CPU/memory 限制及最小环境变量。
- 实现 deadline；超时必须 kill、wait、remove；正常/异常路径都 remove；收集截断 stdout/stderr 和受限文件元数据。
- 验证：fake runtime 契约、无限循环/启动失败/超时/输出越限/文件越限/路径越界/清理测试。

### 4. 配置选择和生产 fail-closed 边界

- 将 execution mode、image 和资源限制纳入类型化 Settings；development/test 默认 local，production 固定 container。
- 在应用边界提供工厂/选择器；禁止请求、模型输出或用户代码选择后端。
- 对 production+local、缺 image、runtime 不可用和容器失败返回稳定错误，确认不创建 local executor。
- 验证：配置契约、工厂测试和 production fail-closed 回归测试。

### 5. Agent/任务执行路径接入统一执行接口

- 在 `DataAnalysisAgent`/M08 兼容适配层注入 `CodeExecutionBackend`，把旧代码执行结果映射到 `ExecutionRequest/Result`。
- 保留外部兼容 API，但避免 Agent 直接实例化进程内 `CodeExecutor`；任务 ID、输出目录和限制由编排/配置层提供。
- 确保执行失败反馈仍能回到 Agent，取消/失败终态不会继续执行新代码。
- 验证：agent contract、integration flow、production path spy/fake backend 测试。

### 6. 安全回归、审计元数据和文档

- 覆盖命令执行、子进程、网络、密钥环境变量、宿主源码、输入写入和输出目录越界等回归行为。
- 保存最小执行审计信息：task ID、代码哈希、后端、耗时、退出状态、限制标志、输出文件元数据和错误码。
- 更新配置样例、README 和运行说明，明确 Docker 是 production 前置条件、local 仅开发/测试。
- 验证：安全回归测试、隐私断言和文档示例检查。

### 7. 全量验证与 whole-branch review

- 运行完整离线测试、类型/静态检查（若项目已有配置）和打包导入验证。
- 对整个分支执行一次独立代码审查，逐条对照设计与验收标准，修复 Critical/Important 问题后重新验证。
- 最后按 finishing-a-development-branch 流程报告分支状态和集成选项。

## Commit checkpoints

- 每个任务完成并通过任务级复核后单独提交，提交信息包含任务编号。
- 不把未验证的安全边界或生产回退逻辑带入后续任务。
