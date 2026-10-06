
# 前端真实 Session 认证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将前端从本地开发 UUID 身份切换到后端 HttpOnly Session Cookie 认证，并完成登录、会话恢复、路由保护和退出登录。

**Architecture:** 使用 AuthProvider 在 React 内存中维护 AuthUser 和认证状态，应用启动时调用 /auth/me 恢复 Session。ApiClient 对所有请求固定使用 credentials: "include"，不再读取 localStorage 或发送 X-User-ID；登录和退出由认证 API 服务封装，受保护路由只依赖 AuthProvider。

**Tech Stack:** React 19、React Router 6、TanStack Query 5、TypeScript、Vite、Vitest、Testing Library、现有 FastAPI /api/auth/* 接口。

## Global Constraints

- 不修改后端认证接口、数据库迁移、Cookie 配置、Docker 或环境变量。
- 不把密码、Session Token、Cookie 或用户身份写入 localStorage、普通 Cookie、URL、日志或错误详情。
- 所有认证和业务 API 请求都必须携带 credentials: "include"。
- 生产前端不得生成或发送 X-User-ID。
- 不提供注册、密码找回、修改密码、多因素认证或用户管理 UI。
- 沿用现有 ApiError、请求超时、错误响应和 Vite /api 代理。
- 每项任务先写测试并确认失败，再写最小实现；提交时只暂存本任务相关文件，不带入已有环境配置变更。
- 完成前必须运行 npm test -- --run、npm run typecheck、npm run build 和 git diff --check。

---

## 文件结构与职责

| 文件 | 责任 |
| --- | --- |
| frontend/src/types/auth.ts | 后端 AuthUserResponse、登录凭据和认证状态类型 |
| frontend/src/services/api/auth.ts | /auth/me、/auth/login、/auth/logout API 函数 |
| frontend/src/services/api/client.ts | 统一 credentials 请求行为和通用错误解析 |
| frontend/src/app/auth.tsx | AuthProvider、认证状态机、会话恢复和登录/退出动作 |
| frontend/src/app/App.tsx | 将 AuthProvider 放在路由上层并位于 QueryClientProvider 内 |
| frontend/src/pages/LoginPage.tsx | 邮箱密码登录表单和登录错误展示 |
| frontend/src/app/router.tsx | 基于 AuthProvider 的加载、错误、未登录重定向和受保护路由 |
| frontend/src/components/layout/AppLayout.tsx | 展示认证用户和调用退出登录 |
| frontend/src/*/*.test.tsx、frontend/src/services/**/*.test.ts | 认证行为和原有页面回归测试 |
| frontend/src/services/session.ts、frontend/src/types/session.ts | 删除开发身份持久化实现 |

### Task 1: 认证类型、API 服务和请求客户端

**Files:**

- Create: frontend/src/types/auth.ts
- Create: frontend/src/services/api/auth.ts
- Modify: frontend/src/services/api/client.ts
- Modify: frontend/src/services/api/client.test.ts
- Create: frontend/src/services/api/auth.test.ts
- Delete: frontend/src/services/session.ts
- Delete: frontend/src/types/session.ts
- Modify: frontend/src/services/api/resources.test.ts

**Interfaces:**

- AuthUser：user_id: string、email: string、role: "USER" | "ADMIN"、is_active: boolean、created_at: string。
- LoginCredentials：email: string、password: string。
- getCurrentUser(): Promise<AuthUser>。
- login(credentials: LoginCredentials): Promise<AuthUser>。
- logout(): Promise<void>。

#### Step 1: Write the failing API-client test

在 frontend/src/services/api/client.test.ts 删除 saveSession 导入和 UUID fixture，新增测试：

~~~tsx
it("includes the session cookie and never sends the development user header", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ ok: true }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  ));
  vi.stubGlobal("fetch", fetchMock);

  await expect(apiClient.get<{ ok: boolean }>("/health")).resolves.toEqual({ ok: true });

  const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
  expect(requestInit.credentials).toBe("include");
  expect(new Headers(requestInit.headers).has("X-User-ID")).toBe(false);
});
~~~

#### Step 2: Run the focused test and verify it fails

Run from frontend：

~~~text
npm exec vitest run src/services/api/client.test.ts -t "includes the session cookie"
~~~

Expected: FAIL because the current client does not set credentials and still has development-session logic.

#### Step 3: Write the failing auth-service contract tests

Create frontend/src/services/api/auth.test.ts with a shared response fixture and three tests that inspect method, URL path and JSON body：

~~~tsx
const user = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "13634930829@163.com",
  role: "ADMIN" as const,
  is_active: true,
  created_at: "2026-10-06T00:00:00Z",
};

it("loads the current user", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(user));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getCurrentUser()).resolves.toEqual(user);
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/auth/me");
  expect((fetchMock.mock.calls[0]?.[1] as RequestInit).credentials).toBe("include");
});

it("logs in with email and password", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(user));
  vi.stubGlobal("fetch", fetchMock);

  await expect(login({ email: user.email, password: "test-password-1234" })).resolves.toEqual(user);
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(init.method).toBe("POST");
  expect(JSON.parse(String(init.body))).toEqual({ email: user.email, password: "test-password-1234" });
});

it("logs out with an empty 204 response", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(logout()).resolves.toBeUndefined();
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/auth/logout");
});
~~~

The helper jsonResponse should return a JSON Response with status 200 and application/json content type. Each test must unstub globals in afterEach.

#### Step 4: Run auth-service tests and verify they fail

~~~text
npm exec vitest run src/services/api/auth.test.ts
~~~

Expected: FAIL because types/auth.ts and services/api/auth.ts do not exist.

#### Step 5: Write the minimal implementation

Create frontend/src/types/auth.ts：

~~~tsx
export type AuthRole = "USER" | "ADMIN";

export interface AuthUser {
  user_id: string;
  email: string;
  role: AuthRole;
  is_active: boolean;
  created_at: string;
}

export interface LoginCredentials {
  email: string;
  password: string;
}
~~~

Create frontend/src/services/api/auth.ts：

~~~tsx
import { apiClient } from "@/services/api/client";
import type { AuthUser, LoginCredentials } from "@/types/auth";

export function getCurrentUser(): Promise<AuthUser> {
  return apiClient.get<AuthUser>("/auth/me");
}

export function login(credentials: LoginCredentials): Promise<AuthUser> {
  return apiClient.post<AuthUser>("/auth/login", credentials);
}

export function logout(): Promise<void> {
  return apiClient.post<void>("/auth/logout");
}
~~~

In frontend/src/services/api/client.ts, remove the getSession import and the X-User-ID block. Add credentials: "include" to fetch while preserving method, headers, body, signal, timeout and existing error parsing：

~~~tsx
const response = await fetch(apiUrl(path), {
  ...init,
  credentials: "include",
  headers: Object.fromEntries(requestHeaders.entries()),
  signal: init.signal ?? controller.signal,
});
~~~

Delete the development-only session.ts and types/session.ts. Remove their setup imports and saveSession calls from resources.test.ts; resource tests do not need a fake identity because the API client now uses the browser Session Cookie.

#### Step 6: Run focused tests and verify they pass

~~~text
npm exec vitest run src/services/api/client.test.ts src/services/api/auth.test.ts src/services/api/resources.test.ts
~~~

Expected: PASS, including existing 204, error-contract, network-error, task-body, FormData and artifact tests.

#### Step 7: Commit Task 1

~~~text
git add frontend/src/types/auth.ts frontend/src/services/api/auth.ts frontend/src/services/api/client.ts frontend/src/services/api/client.test.ts frontend/src/services/api/auth.test.ts frontend/src/services/api/resources.test.ts frontend/src/services/session.ts frontend/src/types/session.ts
git commit -m "feat: use session cookies in frontend api client"
~~~

### Task 2: AuthProvider 与会话恢复

**Files:**

- Create: frontend/src/app/auth.tsx
- Create: frontend/src/app/auth.test.tsx
- Modify: frontend/src/app/App.tsx

**Interfaces:**

- AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error"。
- AuthContextValue：status、user、bootstrapError、isLoggingIn、isLoggingOut、retryBootstrap、login、logout。
- useAuth(): AuthContextValue；Provider 外调用抛出明确错误。

#### Step 1: Write failing provider tests

Create frontend/src/app/auth.test.tsx. Use a QueryClientProvider with a new QueryClient and a test consumer that renders status、user?.email and retry/login/logout buttons. Add these cases：

~~~tsx
it("restores a valid server session", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(user)));

  renderWithAuth(<AuthProbe />);

  expect(screen.getByText("loading")).toBeInTheDocument();
  expect(await screen.findByText("authenticated")).toBeInTheDocument();
  expect(screen.getByText(user.email)).toBeInTheDocument();
});

it("treats a 401 session lookup as unauthenticated without an error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(apiErrorResponse("AUTHENTICATION_REQUIRED", 401)));

  renderWithAuth(<AuthProbe />);

  expect(await screen.findByText("unauthenticated")).toBeInTheDocument();
  expect(screen.queryByText("error")).not.toBeInTheDocument();
});

it("exposes a retry action for session lookup failures", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(apiErrorResponse("NETWORK_ERROR", 503))
    .mockResolvedValueOnce(jsonResponse(user));
  vi.stubGlobal("fetch", fetchMock);

  renderWithAuth(<AuthProbe />);
  expect(await screen.findByText("error")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "retry" }));
  expect(await screen.findByText("authenticated")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
~~~

Add login and logout tests that verify the provider updates user, sets the loading flags, calls the API through credentials: "include", clears QueryClient cache, and ends in unauthenticated after logout.

#### Step 2: Run provider tests and verify they fail

~~~text
npm exec vitest run src/app/auth.test.tsx
~~~

Expected: FAIL because AuthProvider and useAuth do not exist.

#### Step 3: Implement the provider state machine

Create frontend/src/app/auth.tsx with：

~~~tsx
export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "error";

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  bootstrapError: ApiError | null;
  isLoggingIn: boolean;
  isLoggingOut: boolean;
  retryBootstrap: () => void;
  login: (credentials: LoginCredentials) => Promise<AuthUser>;
  logout: () => Promise<void>;
}
~~~

Use a monotonically increasing useRef request sequence for /auth/me; only the latest bootstrap response may update state. On success set authenticated and user; on ApiError.status === 401 set unauthenticated with no bootstrap error; on other failures set error and preserve the ApiError for the retry view. Run the first bootstrap from useEffect and expose retry by invoking the same function with a new sequence number.

login must set isLoggingIn, call the API login function, clear the QueryClient before setting the returned user, set authenticated, and rethrow failures after resetting the flag. logout must set isLoggingOut, call the API logout function, clear the QueryClient and set unauthenticated in a finally block, then rethrow request failures so the layout can notify the user. The provider must never write to localStorage.

#### Step 4: Mount AuthProvider inside the existing provider stack

Modify frontend/src/app/App.tsx：

~~~tsx
export function App() {
  return (
    <AppProviders>
      <AuthProvider>
        <AppRouter />
      </AuthProvider>
    </AppProviders>
  );
}
~~~

The placement ensures AuthProvider can use the existing QueryClientProvider while the router and all pages can call useAuth.

#### Step 5: Run provider and smoke tests

~~~text
npm exec vitest run src/app/auth.test.tsx src/app/smoke.test.tsx
~~~

Expected: PASS.

#### Step 6: Commit Task 2

~~~text
git add frontend/src/app/auth.tsx frontend/src/app/auth.test.tsx frontend/src/app/App.tsx
git commit -m "feat: add frontend auth provider"
~~~

### Task 3: 登录页和受保护路由

**Files:**

- Modify: frontend/src/pages/LoginPage.tsx
- Modify: frontend/src/app/router.tsx
- Modify: frontend/src/pages/pages.test.tsx
- Modify: frontend/src/app/router.test.tsx

**Interfaces:**

- Login form submits { email: string; password: string } to useAuth().login.
- ProtectedRoute receives children and obtains all auth state from useAuth.
- Redirect state stores only { from: string }, where from is the current internal pathname plus search and hash.

#### Step 1: Write failing login and routing tests

Replace the current UUID/display-name test in pages.test.tsx with an AuthProvider-backed test. Stub the first /auth/me call as 401, then the login call as 200：

~~~tsx
it("logs in with email and password and returns to the protected page", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(apiErrorResponse("AUTHENTICATION_REQUIRED", 401))
    .mockResolvedValueOnce(jsonResponse(user));
  vi.stubGlobal("fetch", fetchMock);

  renderLoginWithAuth("/login", "/login?from=tasks");
  await screen.findByLabelText("邮箱");
  fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: user.email } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "test-password-1234" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByText("受保护工作区")).toBeInTheDocument();
  expect(localStorage.length).toBe(0);
  expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ credentials: "include", method: "POST" });
});

it("shows a stable error for invalid credentials", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(apiErrorResponse("AUTHENTICATION_REQUIRED", 401))
    .mockResolvedValueOnce(apiErrorResponse("INVALID_CREDENTIALS", 401));
  vi.stubGlobal("fetch", fetchMock);

  renderLoginWithAuth();
  fireEvent.change(await screen.findByLabelText("邮箱"), { target: { value: user.email } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "wrong-password" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("邮箱或密码错误");
});
~~~

Update router.test.tsx to cover loading state before /auth/me resolves, 401 redirect to login with original path, successful /auth/me rendering, and non-401 bootstrap failure showing 重试 without redirecting.

#### Step 2: Run focused page and router tests and verify they fail

~~~text
npm exec vitest run src/pages/pages.test.tsx src/app/router.test.tsx
~~~

Expected: FAIL because the tests still find the development form and ProtectedRoute still reads the deleted session service.

#### Step 3: Implement the login page

Replace UUID and display-name state in LoginPage.tsx with email、password、isLoggingIn、login and user from useAuth. Submit behavior：

~~~tsx
const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
  event.preventDefault();
  setError(null);
  try {
    await login({ email: email.trim(), password });
    navigate(redirectPath(location.state), { replace: true });
  } catch (caught) {
    setError(toLoginErrorMessage(caught));
  }
};
~~~

Use type=email、autoComplete=email、type=password and autoComplete=current-password. Keep password out of DOM text, URL and persistent storage. Map INVALID_CREDENTIALS to 邮箱或密码错误；NETWORK_ERROR and REQUEST_TIMEOUT to 无法连接认证服务，请确认后端已启动。；all other failures to 登录未完成，请稍后重试。. The submit button is 登录 and disabled while isLoggingIn.

Add a local redirectPath helper that accepts only a string beginning with / and not //; otherwise return /datasets. On an authenticated login route, navigate to the same safe destination in an effect.

#### Step 4: Implement AuthProvider-based route protection

In router.tsx, remove getSession and import PageState and useAuth. Render：

~~~tsx
if (status === "loading") {
  return <PageState kind="loading" title="正在验证登录状态" description="请稍候。" />;
}
if (status === "error") {
  return <PageState kind="error" title="认证服务暂时不可用" description="请检查 API 服务后重试。" action={{ label: "重试", onClick: retryBootstrap }} />;
}
if (status === "unauthenticated") {
  const from = location.pathname + location.search + location.hash;
  return <Navigate to="/login" replace state={{ from }} />;
}
return <>{children}</>;
~~~

The root and protected routes remain unchanged apart from the new auth gate. A /me 401 can redirect to login, while a backend outage remains visible as a retryable error.

#### Step 5: Run focused tests and verify they pass

~~~text
npm exec vitest run src/pages/pages.test.tsx src/app/router.test.tsx
~~~

Expected: PASS with no UUID or display-name selectors remaining.

#### Step 6: Commit Task 3

~~~text
git add frontend/src/pages/LoginPage.tsx frontend/src/app/router.tsx frontend/src/pages/pages.test.tsx frontend/src/app/router.test.tsx
git commit -m "feat: connect frontend login to session auth"
~~~

### Task 4: 布局用户信息、退出登录和查询缓存

**Files:**

- Modify: frontend/src/components/layout/AppLayout.tsx
- Modify: frontend/src/components/layout/AppLayout.test.tsx

**Interfaces:**

- AppLayout consumes user、logout and isLoggingOut from useAuth.
- Logout failures are reported through useNotifications using existing ApiError request ID behavior.

#### Step 1: Write failing layout tests

Replace local-session setup with an AuthProvider wrapper whose /auth/me request returns the admin fixture. Add assertions：

~~~tsx
it("renders the authenticated email and role", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(user)));
  renderLayoutWithAuth();

  expect(await screen.findByText(user.email)).toBeInTheDocument();
  expect(screen.getByText("管理员")).toBeInTheDocument();
});

it("calls the backend logout endpoint and navigates to login", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(jsonResponse(user))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  renderLayoutWithAuth();

  await screen.findByText(user.email);
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

  expect(await screen.findByText("登录页")).toBeInTheDocument();
  expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/auth/logout");
  expect((fetchMock.mock.calls[1]?.[1] as RequestInit).credentials).toBe("include");
});
~~~

Also verify that a failed logout still redirects to login and creates an error notification without exposing the backend error body.

#### Step 2: Run layout tests and verify they fail

~~~text
npm exec vitest run src/components/layout/AppLayout.test.tsx
~~~

Expected: FAIL because AppLayout still imports and renders the deleted local session.

#### Step 3: Implement layout integration

In AppLayout.tsx:

- replace getSession/clearSession with useAuth;
- derive the avatar initial from user.email;
- render the email and 用户/管理员 role label;
- set disabled={isLoggingOut} on the logout button;
- call await logout() and navigate to /login in finally;
- in catch, notify with ApiError.message and requestId, or 退出登录未完成，请稍后重试。 for unknown errors;
- retain mobile-navigation closing behavior.

AuthProvider already clears the QueryClient in logout's finally block, so no user-specific cache survives the transition. Login also clears the cache before the new user is rendered.

#### Step 4: Run layout and page regression tests

~~~text
npm exec vitest run src/components/layout/AppLayout.test.tsx src/pages/pages.test.tsx
~~~

Expected: PASS.

#### Step 5: Commit Task 4

~~~text
git add frontend/src/components/layout/AppLayout.tsx frontend/src/components/layout/AppLayout.test.tsx
git commit -m "feat: add session logout to app layout"
~~~

### Task 5: 清理开发认证残留并完成验收

**Files:**

- Modify: frontend/src/services/api/client.test.ts
- Modify: frontend/src/services/api/resources.test.ts
- Modify: frontend/src/pages/pages.test.tsx
- Modify: frontend/src/app/router.test.tsx
- Modify: frontend/src/components/layout/AppLayout.test.tsx
- Modify: frontend/src/app/auth.test.tsx
- Delete: frontend/src/services/session.test.ts

#### Step 1: Search for stale development-auth references

Run from repository root：

~~~text
rg -n "getSession|saveSession|clearSession|DEFAULT_DEV_USER_ID|isValidUserId|X-User-ID|DevSession|开发用户 UUID|显示名称" frontend/src
~~~

Expected: no matches. The listed test files must use AuthProvider fixtures or no identity setup; do not remove intentional explanatory text from the design document.

#### Step 2: Run the complete frontend test suite

~~~text
Set-Location frontend
npm test -- --run
~~~

Expected: all Vitest tests pass, including API resources, page states, data tables, upload controls, pages, router, auth provider and layout.

#### Step 3: Run type checking and production build

~~~text
npm run typecheck
npm run build
~~~

Expected: TypeScript exits with code 0 and Vite creates the production bundle without missing-import or unreachable-auth-state errors.

#### Step 4: Check the final diff for secrets and formatting

From the repository root：

~~~text
git diff --check HEAD~5..HEAD
git diff -- frontend/src
rg -n "sk-[A-Za-z0-9]|password\\s*[:=].*(secret|change-me)|session[_ -]?token|document\\.cookie|localStorage" frontend/src
~~~

Expected: no whitespace errors, no API keys or password values, no Cookie access, and no local identity persistence. Existing user configuration changes outside frontend/src must remain outside the auth commits.

#### Step 5: Commit cleanup and verification changes

~~~text
git add frontend/src
git commit -m "test: verify frontend session authentication"
~~~

The final working tree may still contain the user's earlier environment and planning changes; do not reset, clean or stage those unrelated files.
