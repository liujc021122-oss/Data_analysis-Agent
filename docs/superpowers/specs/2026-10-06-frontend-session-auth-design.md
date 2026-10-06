# 前端真实 Session 认证设计

## 1. 目标与范围

当前前端仍使用本地开发身份：登录页让用户输入 UUID 和显示名称，随后把身份写入 `localStorage`，API 请求再附加 `X-User-ID`。后端已经完成真实认证，提供服务端 Session、HttpOnly Cookie 以及以下接口：

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/auth/register`（本阶段不提供前端注册入口）

本次改造的目标是让前端使用后端真实 Session 完成登录、会话恢复、路由保护和退出登录。

本阶段包含：

- 登录邮箱和密码表单；
- 通过 `credentials: "include"` 发送同源 API 请求；
- 应用启动时通过 `/api/auth/me` 恢复 HttpOnly Cookie 对应的用户；
- 受保护路由的加载态、未登录跳转和认证失败处理；
- 布局中展示后端返回的用户信息，并调用真实退出接口；
- 删除前端 UUID、显示名称和 `X-User-ID` 开发认证路径；
- 覆盖认证请求、会话恢复、登录、退出和路由保护的前端测试。

本阶段不包含：

- 注册、密码找回、修改密码、多因素认证；
- 用户管理、组织和项目成员 UI；
- JWT、前端 Token 存储或新的认证协议；
- 后端认证接口、数据库和 Cookie 配置的修改。

## 2. 方案选择

### 2.1 推荐方案：内存中的 AuthProvider + 后端 HttpOnly Session

前端只在 React 内存中保存当前用户对象和认证状态。登录成功后，浏览器自动保存后端设置的 HttpOnly Cookie；前端不能读取 Cookie，也不保存密码、Session Token 或用户身份到 `localStorage`。

所有 API 请求统一设置 `credentials: "include"`。Vite 开发服务器通过现有 `/api` 代理转发到后端，因此浏览器看到的是同源请求，不需要新增 CORS 配置。

应用启动时只以 `/api/auth/me` 的响应决定会话状态：

- `200`：恢复用户并允许访问受保护页面；
- `401`：判定为未登录，允许访问登录页；
- 网络错误或 `5xx`：保持认证状态未知，显示可重试的服务不可用状态，不把后端故障误判成密码错误。

### 2.2 不采用的方案

- **继续使用 `localStorage` 身份**：无法证明身份，且会绕过后端 Session，不符合已经完成的认证边界。
- **把 Session Token 写入 `localStorage` 或普通 Cookie**：扩大 XSS 泄露面，并重复实现浏览器已经提供的 Cookie 管理。
- **前端自行解析或刷新 Cookie**：HttpOnly Cookie 的设计就是禁止脚本读取；Session 生命周期由后端控制。

## 3. 组件与模块边界

### 3.1 认证类型与 API 服务

新增前端认证类型，映射后端 `AuthUserResponse`：

- `user_id: string`
- `email: string`
- `role: "USER" | "ADMIN"`
- `is_active: boolean`
- `created_at: string`

新增认证 API 服务函数，集中调用：

- `getCurrentUser()` -> `GET /auth/me`；
- `login(credentials)` -> `POST /auth/login`；
- `logout()` -> `POST /auth/logout`。

认证服务只负责 API 契约，不负责 React 状态、导航或表单错误展示。

### 3.2 AuthProvider

新增 `AuthProvider` 和 `useAuth`，作为路由的上层 Provider。状态模型为：

```text
status: "loading" | "authenticated" | "unauthenticated" | "error"
user: AuthUser | null
bootstrapError: ApiError | null
isLoggingIn: boolean
isLoggingOut: boolean
```

Provider 暴露：

- `retryBootstrap()`：重新调用 `/auth/me`；
- `login(email, password)`：调用登录接口，成功后写入内存中的 `user`；
- `logout()`：调用退出接口，成功后清空内存中的 `user`。

Provider 挂载后只执行一次初始会话恢复；重试时取消或忽略已经过期请求的结果，避免旧响应覆盖新状态。`401` 只表示未登录，不作为全局错误通知；网络错误和服务端错误保留为可重试错误。

`AuthProvider` 不向业务组件暴露密码、Cookie 或 Token，也不从 `localStorage` 读取认证信息。

### 3.3 API Client

`ApiClient.request` 的 fetch 配置固定包含 `credentials: "include"`，并删除：

- 对 `services/session` 的依赖；
- `X-User-ID` 请求头的生成逻辑。

现有 JSON、`FormData`、超时和 `ApiError` 解析行为保持不变。认证接口的 `204` 退出响应继续按现有空响应逻辑处理。

## 4. 页面和路由行为

### 4.1 登录页

登录页改为邮箱和密码两个字段：

- 邮箱使用 `type="email"` 和合适的自动填充语义；
- 密码使用 `type="password"`，密码只存在于组件状态，提交后不写入任何持久化存储；
- 提交期间禁用提交按钮并避免重复请求；
- 成功后跳转到受保护路由记录的原始地址，若没有记录则跳转 `/datasets`；
- 不提供注册按钮或注册表单。

错误信息按稳定错误码映射为用户可理解的中文提示。`INVALID_CREDENTIALS` 显示统一的“邮箱或密码错误”，不区分邮箱不存在和密码错误；网络错误显示后端不可达提示。错误详情、密码和 Cookie 不显示在页面上。

如果登录页加载时已经恢复出有效用户，则自动跳转到待返回地址或 `/datasets`，避免已登录用户再次提交登录表单。

### 4.2 ProtectedRoute

`ProtectedRoute` 改为读取 `useAuth`，不再同步检查 `localStorage`：

1. `status === "loading"`：显示稳定尺寸的认证加载界面；
2. `status === "error"`：显示服务不可用和“重试”操作，不跳转登录页；
3. `status === "unauthenticated"`：跳转 `/login`，并保存当前 pathname、query 和 hash 作为内部返回地址；
4. `status === "authenticated"`：渲染受保护内容。

只保存应用内部的路由位置，不接受外部 URL，避免登录后的开放重定向。

### 4.3 AppLayout

布局通过 `useAuth` 获取用户，不再调用 `getSession`。用户区域展示后端返回的邮箱，并将 `USER`/`ADMIN` 映射为界面角色文案。

退出登录流程：

1. 调用 `POST /api/auth/logout`；
2. 成功后由 Provider 清空内存用户状态；
3. 导航到 `/login`；
4. 退出期间禁用退出按钮，避免重复提交。

若退出请求失败，仍清空本地内存状态并跳转登录页，同时显示可重试的错误提示。由于 HttpOnly Cookie 不能由前端脚本删除，下一次应用启动仍以 `/auth/me` 的服务端结果为准。

## 5. 数据流

```text
应用启动
  -> AuthProvider 调用 GET /api/auth/me（credentials: include）
  -> 200: user 写入内存 -> ProtectedRoute 渲染工作台
  -> 401: user=null -> ProtectedRoute 跳转登录页
  -> 网络/5xx: status=error -> 显示重试

登录提交
  -> AuthProvider.login(email, password)
  -> POST /api/auth/login（credentials: include）
  -> 后端设置 HttpOnly Session Cookie
  -> 返回 AuthUserResponse -> user 写入内存 -> 返回原始页面

退出登录
  -> AuthProvider.logout()
  -> POST /api/auth/logout（credentials: include）
  -> 后端撤销 Session 并清理 Cookie
  -> user 清空 -> 跳转登录页
```

## 6. 错误和边界处理

- 登录接口返回 `401 INVALID_CREDENTIALS` 时只显示统一凭据错误，不重试，不保存密码。
- `/auth/me` 返回 `401` 时静默进入未登录状态，不弹出全局错误通知。
- `/auth/me` 网络错误、超时或 `5xx` 时显示重试入口，避免用户误以为需要重新输入密码。
- 登录或退出期间组件卸载时，异步结果不得更新已经卸载的组件状态。
- 用户成功登录后，QueryClient 中与匿名状态相关的缓存应失效或重新获取，避免显示上一位用户的旧数据；退出后清理受保护资源查询缓存。
- 所有认证 API 请求沿用现有请求超时和 `ApiError` 规范，不把密码、Session Cookie 或 Token 放入日志、错误详情或测试快照。

## 7. 文件变更边界

预计修改或新增：

- `frontend/src/services/api/client.ts`；
- `frontend/src/services/api/auth.ts`；
- `frontend/src/types/auth.ts`；
- `frontend/src/app/auth.tsx`；
- `frontend/src/app/App.tsx` 或 Provider 组合文件；
- `frontend/src/pages/LoginPage.tsx`；
- `frontend/src/app/router.tsx`；
- `frontend/src/components/layout/AppLayout.tsx`；
- 对应的前端测试文件。

删除或停止使用：

- `frontend/src/services/session.ts`；
- `frontend/src/types/session.ts`；
- 所有开发 UUID、显示名称和 `X-User-ID` 测试路径。

后端认证实现、环境变量、数据库迁移和 Docker 配置不在本次前端改造的文件边界内。

## 8. 测试计划与验收标准

按现有 Vitest + Testing Library 结构补充测试：

1. API Client 默认携带 `credentials: "include"`，不发送 `X-User-ID`。
2. AuthProvider 在 `/auth/me` 成功时恢复用户，在 `401` 时进入未登录，在网络错误时展示可重试状态。
3. 登录成功调用正确接口、更新用户并跳转原始地址；失败显示统一错误，密码不写入 `localStorage`。
4. 登录页不再出现 UUID、显示名称或注册入口。
5. ProtectedRoute 在加载、未登录、认证失败和已登录四种状态下行为正确。
6. AppLayout 展示认证用户邮箱和角色，退出调用后端接口并返回登录页。
7. 登录、退出和会话恢复请求均验证 `credentials: "include"`。
8. 原有数据集、任务和报告页面测试在认证上下文下继续通过。

实现完成的最低验收命令：

```text
cd frontend
npm test -- --run
npm run typecheck
npm run build
```

同时执行 `git diff --check`，确认没有格式错误或意外的密钥、Session Token 和密码写入。
