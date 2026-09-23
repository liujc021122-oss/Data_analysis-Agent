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
7. [ ] 全量验证与 whole-branch review

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
