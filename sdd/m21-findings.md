# M21 RAG 调研发现

- 现有持久化基于 SQLAlchemy/Alembic，生产配置已经要求 MySQL，测试主要使用 SQLite。
- 现有 `UnitOfWork` 是所有数据库仓储的组合入口，新增知识仓储应接入该边界。
- `Storage` 已提供 `put/get/delete/stat/exists`，知识文档需要独立的 `knowledge/{document_id}/...` key namespace。
- `AccessSubject` 已实现 owner/admin 规则；知识文档检索必须把 subject 条件下推到 SQL 查询。
- `AuditWriter` 有 metadata allowlist，知识审计只能记录资源类型、角色、状态码和结果数量等安全标量。
- Worker broker 只携带 `task_id`；任务 metadata 可保存规范化的知识文档 ID 列表，不应携带正文。
- `DataAnalysisAgent` 和 `quick_analysis` 具有旧调用兼容约束；新增知识参数必须是可选 keyword-only 参数。
- `ReportService` 以 Agent 生成的 canonical Markdown 为输入；来源附录应在 Markdown 进入 renderer 前确定性追加。
- 标准 MySQL 没有 pgvector 等价物；M21 使用 JSON 向量和应用层余弦计算，并通过 MySQL dialect DDL 编译测试保证不依赖 PostgreSQL 类型。
