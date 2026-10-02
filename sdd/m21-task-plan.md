# M21 RAG 业务知识库实施跟踪

## 状态

- [x] 需求与 MySQL 方案确认
- [x] 设计文档确认并提交
- [x] 实现计划完成
- [ ] 领域管线
- [ ] MySQL 持久化与迁移
- [ ] 配置、存储与知识服务
- [ ] 知识库 API
- [ ] Worker/Agent/报告接入
- [ ] 文档、全量验证与交付复核

## 关键决策

- 使用标准 MySQL，不使用 PostgreSQL/pgvector。
- 向量存 JSON，权限过滤后在应用层做精确余弦相似度。
- 首版格式为 txt、md、csv、docx；PDF 不在 M21 范围内。
- 知识内容只作为不受信任参考资料，不能成为执行指令。
- 普通用户 owner 隔离，管理员复用现有跨用户授权与审计规则。
- 现有 `task_plan.md`、`findings.md`、`progress.md` 是 M09 历史文件，不修改。

## 错误记录

| 错误 | 尝试 | 处理 |
| --- | ---: | --- |
| `docs/superpowers/` 被 `.gitignore` 忽略 | 1 | 设计文档已使用 `git add -f` 单文件提交；计划提交时同样只强制纳入 M21 文件 |
