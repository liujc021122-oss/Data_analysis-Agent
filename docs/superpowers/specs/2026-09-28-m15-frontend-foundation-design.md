# M15 前端基础框架设计

## 状态

设计已由用户确认，实施目标是建立可独立启动的 React + TypeScript + Vite 单页前端，不实现真实模型调用或复杂分析交互。

## 目标

- 建立 `frontend/` 独立前端工程和约定的 `src/app`、`pages`、`components`、`services`、`hooks`、`types`、`styles` 分层。
- 配置 React Router、TanStack Query、统一 API 客户端和全局错误反馈。
- 提供登录、数据集、新建分析、任务详情、报告详情、历史任务六个页面骨架。
- 提供可复用的按钮、表格、弹窗、文件上传和页面状态组件。
- 让页面在 API 无数据、API 不可用、路由刷新和窄屏场景下都保持可用且有明确反馈。

## 非目标

- 不新增后端认证接口；M15 登录仅用于本地开发身份。
- 不调用真实 LLM，不在前端实现分析算法、图表分析或报告生成。
- 不把后端本地路径、存储 URI、prompt、原始执行输出或密钥展示给用户。
- 不引入重量级 UI 组件库；基础组件使用 React、CSS 变量和 Lucide 图标组合实现。
- 不要求 SSE 成为启动或核心页面的前置依赖。M14 SSE 契约保留为可选增强，REST 事件接口是当前主流程。

## 技术方案

### 工程与依赖

前端使用 Vite 的 React TypeScript 模板。运行时依赖为 React、React DOM、React Router、TanStack Query 和 `lucide-react`；请求使用浏览器原生 `fetch`，以保留对错误响应、文件上传和二进制报告内容的精确控制。开发依赖包含 TypeScript、Vite、Vitest、jsdom 和 Testing Library，用于类型检查、构建和关键行为测试。

### 目录边界

```text
frontend/
├── index.html
├── package.json
├── tsconfig*.json
├── vite.config.ts
└── src/
    ├── app/          # 应用入口、路由、QueryProvider、全局错误边界
    ├── pages/        # 路由页面，只编排数据和页面结构
    ├── components/   # 无业务或低业务耦合的复用组件
    ├── services/     # API client、认证会话、资源请求函数
    ├── hooks/        # Query/mutation hooks 和响应式交互逻辑
    ├── types/        # 与后端 DTO 对齐的公共 TypeScript 类型
    └── styles/       # 设计 token、全局样式、响应式布局
```

页面不直接拼接 URL 或读取 `localStorage`。页面通过 hooks 获取数据，hooks 通过 services 访问 API，服务层只返回已解析的类型或统一的 `ApiError`。

### 认证与 API 客户端

开发登录页保存 `{ userId, displayName }` 到 `localStorage` 的版本化键中。默认用户 UUID 可由 `VITE_DEV_USER_ID` 配置，用户可在登录页修改；客户端每次请求自动附加 `X-User-ID`。会话缺失时，受保护路由重定向到 `/login`。

API 基地址来自 `VITE_API_BASE_URL`，为空时使用同源 `/api`。客户端统一处理：

- JSON 请求和响应；
- `multipart/form-data` 数据集上传；
- `204` 空响应；
- 后端 `{ code, message, details, request_id }` 错误；
- 网络异常、超时和非 JSON 错误的安全兜底。

错误对象包含 `code`、用户可见 `message`、`details` 和可选 `requestId`。TanStack Query 的全局 mutation/query error handler 将 API 错误转成统一通知；页面级 Error 状态保留重试动作，认证错误清除会话并回到登录页。

### 后端资源映射

类型与当前 API DTO 对齐：`Dataset`、`DatasetProfile`、`AnalysisTask`、`TaskEvent`、`Artifact`、`PageResponse<T>`、`TaskSubmissionResponse` 和 `ApiErrorResponse`。服务层覆盖以下 REST 端点：

| 前端能力 | API |
| --- | --- |
| 数据集列表/上传/删除 | `GET/POST/DELETE /api/datasets` |
| 创建/列表/查看任务 | `POST/GET /api/analysis-tasks`、`GET /api/analysis-tasks/{id}` |
| 任务事件 | `GET /api/analysis-tasks/{id}/events` |
| 取消/重试任务 | `POST /api/analysis-tasks/{id}/cancel`、`/retry` |
| 报告/artifact 元信息 | `GET /api/artifacts/{id}` |
| 报告内容下载 | 使用后端返回的授权 `content_url` |

任务详情使用 REST 事件历史展示进度和阶段；未来启用 M14 SSE 时只新增订阅适配，不改变页面数据模型和 REST fallback。

## 路由与页面

| 路由 | 页面职责 |
| --- | --- |
| `/login` | 本地开发身份登录、记住用户、进入工作台 |
| `/datasets` | 数据集列表、上传、删除、统计摘要、空状态 |
| `/analyses/new` | 选择数据集、填写查询、创建分析任务 |
| `/tasks/:taskId` | 状态、阶段、事件时间线、取消/重试、报告 artifact 入口 |
| `/reports/:artifactId` | 报告元信息、授权内容链接、打开/下载操作 |
| `/tasks/history` | 历史任务列表、状态筛选、分页、进入详情 |

登录之外的页面使用统一 `AppLayout`：桌面侧边导航和顶栏、窄屏抽屉导航、页面标题区和内容区。刷新任一路由由 Vite fallback 正常返回入口文件，React Router 再恢复页面；未知路径进入轻量 NotFound 状态。

## 组件与状态

基础组件包含：

- `Button`：primary、secondary、ghost、danger、loading、disabled；图标按钮带 `aria-label`。
- `DataTable`：列定义、空行、移动端横向可读布局、行点击入口。
- `Modal`：键盘 Escape、焦点语义、遮罩和关闭回调。
- `FileUpload`：CSV 文件类型/大小提示、拖拽与选择、上传中和失败重试。
- `PageState`：Loading、Empty、Error 三种统一页面状态，错误状态提供 retry。
- `StatusBadge`、`ToastProvider`、`ConfirmDialog` 等低耦合基础组件。

视觉采用面向数据工作的浅色工作台：中性背景、深色正文、蓝色主行动色、橙色警示色和红色危险色，颜色全部通过语义 token 管理。布局使用 4/8 间距节奏、最小 44px 交互目标、可见键盘焦点和窄屏断点，不使用 emoji 作为图标，不依赖 hover 才能完成主要操作。

## 数据流与错误处理

```text
路由守卫 -> 页面 -> Query/Mutation Hook -> API Service -> fetch
     ^            |                              |
     |            +-- Loading/Empty/Error <------+-- ApiError
     +---------------- 本地开发会话 <-------------+
```

Query key 按资源和参数稳定组织，mutation 成功后只失效相关列表/详情 query。请求失败不会吞掉后端错误码；全局通知显示安全消息和 request ID，页面级状态提供重试，上传和创建任务按钮在请求期间锁定以避免重复提交。删除数据集和取消任务使用确认弹窗。

## 测试与验收

- API client 测试：开发身份请求头、成功 JSON、204、后端错误契约、网络错误。
- 路由/认证测试：未登录重定向、登录后进入工作台、刷新保护路由。
- 组件测试：按钮 loading、表格空态、上传选择、Modal 关闭、PageState 重试。
- 页面数据测试：数据集和历史任务使用 Query 状态渲染，创建任务提交正确 DTO。
- 工程验证：`npm run typecheck`、`npm run test`、`npm run build`；启动开发服务器后检查入口和受保护路由。

验收以真实 API 契约为边界；当后端服务或任务 broker 不可用时，前端仍能启动并显示统一错误状态，不将该环境限制误判为模型失败。
