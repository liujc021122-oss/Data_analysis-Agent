# M17 用户认证与权限设计

## 1. 目标与范围

M17 为浏览器优先的后端 API 增加真实用户认证、Session 生命周期、角色授权、资源级权限和敏感操作审计，使多个用户可以安全共享系统。

本阶段覆盖：

- 用户注册、登录、退出和当前用户查询；
- Argon2id 密码哈希，不保存密码原文；
- 服务端 Session 和 HttpOnly Cookie；
- `USER` 与 `ADMIN` 两种角色；
- 现有数据集、分析任务、Artifact 和报告的对象级授权；
- 报告/Artifact 元数据和实际文件读取的双重权限检查；
- 认证、授权和敏感业务操作审计；
- 覆盖越权、Session 失效和管理员差异的 API/数据库测试。

本阶段不实现组织、项目和共享成员关系表。现有授权链继续使用：

```text
Principal.user_id -> dataset.user_id -> task.user_id -> artifact/report
```

组织和项目将在后续阶段引入，并通过新的授权策略接入，不在 M17 中伪造实体或迁移现有所有权语义。

## 2. 方案选择

### 2.1 推荐方案：服务端 Session

采用随机、不透明的服务端 Session Token：

- Cookie 只保存高熵随机 Token；
- 数据库只保存 Token 的 SHA-256 哈希；
- 每次请求校验 Session 是否存在、未过期、未撤销且用户仍启用；
- 退出登录将当前 Session 标记为撤销，并清理 Cookie；
- 角色变化或账户停用可以立即阻断既有 Session。

该方案与浏览器 Cookie 模型匹配，撤销简单，管理员权限变化能立即生效，也避免把权限声明长期固化在不可撤销的 JWT 中。

### 2.2 未采用的方案

- JWT access/refresh token：需要额外处理安全存储、刷新轮换、撤销黑名单和跨端生命周期，超出当前 API 的必要范围。
- 本阶段直接建立完整组织/项目层级：能表达更完整的协作模型，但会扩大迁移、API、查询和既有 owner 语义的变更面，不是当前验收标准所要求的能力。

## 3. 分层架构

### 3.1 认证服务层

新增认证服务负责：

- 邮箱规范化和账户创建；
- Argon2id 哈希生成和校验；
- Session 创建、查找、刷新最后访问时间和撤销；
- 认证失败的统一错误；
- 注册、登录、退出和账户停用相关审计。

认证服务只通过 Unit of Work/仓储访问持久化，不直接依赖 FastAPI Request 或 Response。

### 3.2 API 适配层

保留 `api/auth.py` 作为 API 认证适配边界：

- `SessionPrincipalProvider` 从固定 Cookie 读取 Token；
- Provider 将原 Token 哈希后查询 Session；
- 成功时返回不可变的 `Principal(user_id, role)`；
- 缺失、无效、过期、撤销或停用账户统一抛出 `AuthenticationError`；
- `HeaderPrincipalProvider` 只作为测试显式注入的替身，不能由生产或默认开发配置自动启用。

新增 `api/routers/auth.py` 提供认证接口；现有业务路由继续使用 `get_current_principal`，但将 Principal 传给集中式授权服务和 owner-scoped service。

### 3.3 授权层

新增 `AuthorizationService` 或等价的集中策略边界，统一表达：

- 普通用户只能访问 `user_id == principal.user_id` 的资源；
- 管理员可以跨用户访问现有数据集、任务、Artifact 和报告；
- 任务创建时，每个提交的 `dataset_id` 都必须通过同一授权规则；
- 授权失败和资源不存在对外统一为 404，避免通过 ID 探测资源存在性；
- 需要管理员而当前用户不是管理员时返回统一的 403。

仓储查询必须携带 owner 条件或显式的管理员范围，不允许先按 ID 查出对象再在路由中忘记检查 owner。Worker 内部任务推进仍使用已有的 task owner 事实，不受浏览器 Principal 限制。

## 4. 数据模型与迁移

### 4.1 `users` 扩展

在现有 `users` 表增加：

- `email_normalized`：规范化后的邮箱，唯一索引；
- `password_hash`：Argon2id 编码字符串；
- `role`：`USER` 或 `ADMIN`，默认 `USER`；
- `is_active`：默认 `True`。

旧数据允许 `email_normalized` 和 `password_hash` 为空，以保持既有离线测试、数据集上传和任务持久化兼容。缺少凭据的旧用户不能登录，也不能通过空值绕过认证。`UserRecord`、ORM 映射和 `ensure` 必须保持“不覆盖已有认证字段”的行为。

角色来源：注册时若规范化邮箱命中 `AUTH_ADMIN_EMAILS` 配置则创建为 `ADMIN`，否则创建为 `USER`。未配置时所有新用户均为普通用户。既有管理员可在测试 fixture 或受控运维脚本中显式创建。

### 4.2 `auth_sessions`

字段：

- `session_id`：UUID 主键；
- `token_hash`：唯一的 SHA-256 哈希；
- `user_id`：外键到 `users`；
- `created_at`、`expires_at`、`last_seen_at`；
- `revoked_at`：可空。

Session 校验条件为：`revoked_at IS NULL`、`expires_at > now`、用户 `is_active = true`。原始 Token 永远不写入数据库、日志、响应 JSON 或审计 metadata。

### 4.3 `audit_events`

字段：

- `event_id`：UUID 主键；
- `user_id`：可空，支持未识别用户的失败登录；
- `action`：稳定的审计动作代码；
- `target_type`、`target_id`：可空目标标识；
- `success`：布尔值；
- `request_id`：来自现有请求 ID 中间件；
- `occurred_at`；
- `metadata_json`：仅保存 allowlist 元数据。

metadata 不得包含密码、Session Token、原始 Cookie、内部文件路径、数据库连接串或未截断的异常详情。

## 5. API 契约

### 5.1 认证接口

- `POST /api/auth/register`
  - 输入：规范化前的邮箱和 12 至 128 个字符密码；
  - 输出：用户 ID、邮箱、角色、创建时间；
  - 状态：`201`；
  - 不自动创建登录 Session。
- `POST /api/auth/login`
  - 输入：邮箱和密码；
  - 成功：`200`，返回用户信息并设置 `daa_session` Cookie；
  - 失败：统一返回 `401 INVALID_CREDENTIALS`。
- `POST /api/auth/logout`
  - 撤销当前 Cookie 对应 Session 并清理 Cookie；
  - 重复调用或没有 Cookie 时保持幂等。
- `GET /api/auth/me`
  - 需要有效 Session；
  - 返回用户 ID、邮箱、角色和启用状态。

### 5.2 Cookie

默认属性：

- `HttpOnly=True`；
- `SameSite=Lax`；
- `Path=/`；
- 固定 Max-Age/Expires；
- 生产环境 `Secure=True`。

Cookie 名称、Session TTL 和 Secure 行为由 Settings 控制；生产环境不能通过缺省配置退回 Header 认证。

### 5.3 统一错误

继续使用现有 `ErrorResponse` 和请求 ID：

- `401 AUTHENTICATION_REQUIRED`：没有可用认证上下文；
- `401 INVALID_CREDENTIALS`：邮箱或密码错误，或账户已停用；
- `409 EMAIL_ALREADY_REGISTERED`：邮箱已被注册；
- `403 PERMISSION_DENIED`：已登录但缺少管理员权限；
- `404` 资源错误：他人资源和未知资源使用对应业务的 not-found code。

错误响应不能包含内部 SQL、存储路径、哈希、Cookie 或异常原文。

## 6. 现有资源授权

### 数据集

- 上传和删除归属于当前 Principal；
- 列表和详情按 owner 过滤；
- 管理员可以跨用户读取和管理；
- 数据集 ID 用于创建任务时必须重新做同一权限检查。

### 分析任务

- 创建任务的 owner 是当前用户；
- 列表、详情、事件、取消和重试使用 Principal 范围；
- 普通用户无法通过修改 task ID 访问或操作他人任务；
- 管理员可以跨用户访问现有管理操作；
- 幂等键仍按用户隔离。

### Artifact/报告

- Artifact 权限通过其任务 owner 解析；
- 报告元数据、下载 URL 和实际内容都必须检查 Principal；
- 下载 URL 生成和 `/content` 读取各执行一次 owner/role 检查；
- 存储对象大小、哈希、存在性和签名/过期时间继续由现有 FileAccessService 校验；
- 客户端不获得原始本地路径或对象存储内部 URI。

## 7. 审计范围

至少记录以下动作：

- `REGISTERED`、`LOGIN_SUCCEEDED`、`LOGIN_FAILED`、`LOGGED_OUT`；
- `DATASET_UPLOADED`、`DATASET_DELETED`；
- `TASK_CREATED`、`TASK_CANCELLED`、`TASK_RETRIED`；
- `ARTIFACT_DOWNLOAD_SUCCEEDED`、`ARTIFACT_DOWNLOAD_DENIED`；
- `AUTHENTICATION_DENIED`、`AUTHORIZATION_DENIED`；
- 管理员跨用户访问动作。

审计写入失败不能泄露内部错误或改变既有业务错误契约；对敏感写操作需要保证业务变更和审计记录在同一 Unit of Work 中提交，或明确记录审计失败并让操作失败，避免产生无审计的高风险成功操作。

## 8. 测试策略

按 TDD 分组实施，每组先添加能正确失败的测试，再写最小实现：

1. 密码哈希和凭据校验：哈希非明文、错误密码失败、密码策略边界。
2. 数据库迁移和仓储：用户唯一邮箱、Session 过期/撤销、旧用户兼容、审计记录。
3. 认证 API：注册、登录、Cookie 属性、`/me`、退出后旧 Cookie 失效。
4. 认证边界：默认应用不接受 `X-User-ID`，生产拒绝 Header Provider。
5. 对象级越权：跨用户任务、数据集、Artifact 和报告的 ID 替换全部失败。
6. 角色差异：普通用户被拒绝管理员范围，管理员可以跨用户访问。
7. 下载安全：生成地址、实际内容读取、签名篡改、过期、文件哈希变化和注销后的访问。
8. 审计安全：敏感动作有记录，记录不包含密码、Token、Cookie、路径和数据库细节。
9. 全量回归：已有离线 Agent、Worker、存储和 API 契约继续通过。

## 9. 交付边界与验证

实现交付包括：数据库迁移、认证/授权服务、API 路由、配置样例、测试和 README 的认证启动说明。不会实现组织管理 UI、项目成员邀请、密码找回、多因素认证或完整用户管理后台。

最终验证至少包括：目标认证/授权测试、全量 pytest、`compileall -q src`、Alembic head 升级/重复升级、`git diff --check`，以及生产配置无法启用 Header Provider 的静态/运行时测试。
