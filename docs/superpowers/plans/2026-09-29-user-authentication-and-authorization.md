# M17 用户认证与权限实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 FastAPI/SQLAlchemy API 增加安全的注册、登录、退出、服务端 Session、角色和对象级权限，并覆盖数据集、任务、Artifact、报告下载及敏感操作审计。

**Architecture:** 使用服务端 opaque Session Token：浏览器只保存 HttpOnly Cookie，数据库只保存 Token 哈希。认证服务负责凭据和 Session 生命周期，API Provider 将有效 Session 转为 `Principal`，集中授权服务把普通用户限制到自己的 `user_id` 范围并允许管理员跨用户访问；现有资源继续沿 `user_id -> task -> artifact/report` 链路授权。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、SQLite/MySQL、`argon2-cffi` Argon2id、pytest、FastAPI TestClient。

## Global Constraints

- 生产和默认开发环境不得自动启用 `X-User-ID` Header Provider；它只能由测试显式注入。
- 密码使用 Argon2id，Session Token 使用至少 32 字节随机值，数据库保存 SHA-256 Token 哈希，不保存原 Token。
- Session Cookie 默认 `HttpOnly=True`、`SameSite=Lax`、`Path=/`；生产环境强制 `Secure=True`。
- 未登录统一返回 `401 AUTHENTICATION_REQUIRED`；他人资源和未知资源统一返回 404；错误响应不能泄露 SQL、路径、密码、哈希或 Cookie。
- 普通用户只访问自己的资源；管理员可以跨用户读取、下载和执行已有任务管理操作。
- 文件下载在 URL 创建和实际内容读取时各检查一次 Principal、Session、签名/过期、文件存在性、大小和哈希。
- 旧的无凭据 `users` 行继续支持离线数据集/任务流程，但不能直接登录；`ensure` 不能覆盖已有凭据或角色。
- 每个行为变更都遵循 RED -> GREEN -> REFACTOR；没有先观察到失败测试，不写生产实现。
- 测试命令使用 `E:\anaconda\python.exe -m pytest`；不使用 WindowsApps 的 `python` stub。
- 每个任务完成后运行该任务的聚焦测试并提交；不将现有未跟踪 `worktrees/` 加入提交。

---

## 文件结构与职责

### 新增文件

- `src/data_analysis_agent/services/auth.py`：密码哈希、用户认证、Session 生命周期和认证用例。
- `src/data_analysis_agent/services/authorization.py`：Principal 的资源范围和角色策略。
- `src/data_analysis_agent/api/routers/auth.py`：注册、登录、退出、当前用户接口。
- `tests/services/test_auth.py`：密码哈希单元契约。
- `tests/database/test_auth_persistence.py`：认证仓储、Session、审计持久化契约。
- `tests/services/test_auth_service.py`：注册、登录和 Session 生命周期契约。
- `tests/api/test_auth.py`：Cookie API、生产边界和认证流程。
- `tests/api/test_authorization.py`：资源级越权和管理员差异。
- `tests/api/test_audit.py`：敏感操作审计和敏感字段排除。
- `alembic/versions/20260929_0006_authentication.py`：users 扩展、auth_sessions、audit_events 迁移。

### 修改文件

- `pyproject.toml`、`requirements.txt`：加入 `argon2-cffi`。
- `src/data_analysis_agent/domain/enums.py`：加入 `UserRole` 和 `AuditAction`。
- `src/data_analysis_agent/config/settings.py`：加入 Session、Cookie 和管理员邮箱配置。
- `src/data_analysis_agent/persistence/models.py`：扩展 `UserRecord`，增加 `AuthSessionRecord`、`AuditEventRecord`。
- `src/data_analysis_agent/persistence/orm_models.py`：扩展 `UserORM`，增加 Session/Audit ORM。
- `src/data_analysis_agent/persistence/orm_mappers.py`：增加新 ORM/record 映射并保留旧用户兼容。
- `src/data_analysis_agent/persistence/repositories.py`：增加认证、Session、审计仓储方法。
- `src/data_analysis_agent/persistence/unit_of_work.py`：暴露 `sessions` 和 `audit_events`。
- `src/data_analysis_agent/api/auth.py`：加入 `SessionPrincipalProvider` 和带角色的 `Principal`。
- `src/data_analysis_agent/api/application.py`：组装认证服务、Session Provider 和授权依赖。
- `src/data_analysis_agent/api/app.py`：路由注册、Provider 默认策略和生产配置保护。
- `src/data_analysis_agent/api/schemas.py`：加入认证请求/响应模型。
- `src/data_analysis_agent/api/routers/__init__.py`：导出 auth router。
- `src/data_analysis_agent/api/routers/tasks.py`、`datasets.py`、`artifacts.py`：接入 Principal 范围和审计。
- `src/data_analysis_agent/services/persistence.py`、`src/data_analysis_agent/datasets/service.py`：增加 Principal-aware 资源操作，保留 Worker/旧调用兼容。
- `src/data_analysis_agent/storage/access.py`：让文件访问接受授权主体并对生成/读取双重校验。
- `tests/api/test_app.py`、`test_tasks.py`、`test_datasets.py`、`test_artifacts.py`：显式注入 Header 测试 Provider，避免旧测试暗中依赖生产级认证旁路。
- `.env.development.example`、`.env.test.example`、`.env.production.example`、`README.md`：记录认证配置和启动行为。

---

### Task 1: 角色、配置和 DTO 契约

**Files:**
- Modify: `pyproject.toml`, `requirements.txt`, `src/data_analysis_agent/domain/enums.py`, `src/data_analysis_agent/config/settings.py`, `src/data_analysis_agent/api/schemas.py`
- Create: `tests/config/test_auth_settings.py`, `tests/api/test_auth_schemas.py`

**Interfaces:**
- Produces `UserRole.USER`, `UserRole.ADMIN`, and `AuditAction` string enum values: `REGISTERED`, `LOGIN_SUCCEEDED`, `LOGIN_FAILED`, `LOGGED_OUT`, `DATASET_UPLOADED`, `DATASET_DELETED`, `TASK_CREATED`, `TASK_CANCELLED`, `TASK_RETRIED`, `ARTIFACT_DOWNLOAD_SUCCEEDED`, `ARTIFACT_DOWNLOAD_DENIED`, `AUTHENTICATION_DENIED`, and `AUTHORIZATION_DENIED`.
- Produces `Settings.session_ttl_seconds`, `Settings.session_cookie_name`, `Settings.session_cookie_secure`, and `Settings.auth_admin_emails`.
- Produces `AuthRegisterRequest`, `AuthLoginRequest`, and `AuthUserResponse` DTOs.

- [ ] **Step 1: Write the failing tests for configuration and DTO contracts.**

Add these assertions to `tests/config/test_auth_settings.py`:

```python
def test_auth_settings_parse_ttl_secure_cookie_and_admin_emails():
    from data_analysis_agent.config.settings import load_settings

    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "AUTH_SESSION_TTL_SECONDS": "900",
            "AUTH_SESSION_COOKIE_NAME": "test_session",
            "AUTH_SESSION_COOKIE_SECURE": "true",
            "AUTH_ADMIN_EMAILS": " Admin@Example.com,root@example.com ",
        },
    )

    assert settings.session_ttl_seconds == 900
    assert settings.session_cookie_name == "test_session"
    assert settings.session_cookie_secure is True
    assert settings.auth_admin_emails == ("admin@example.com", "root@example.com")


def test_production_cannot_disable_secure_session_cookie():
    from data_analysis_agent.config.settings import ConfigurationError, load_settings

    with pytest.raises(ConfigurationError, match="secure"):
        load_settings(
            app_env="production",
            environ={
                "APP_ENV": "production",
                "OPENAI_API_KEY": "key",
                "OPENAI_BASE_URL": "https://llm.example",
                "OPENAI_MODEL": "model",
                "DATABASE_URL": "mysql+pymysql://user:pass@db/app",
                "STORAGE_ENDPOINT": "https://s3.example",
                "STORAGE_BUCKET": "bucket",
                "EXECUTION_IMAGE": "analysis:latest",
                "AUTH_SESSION_COOKIE_SECURE": "false",
            },
        )
```

Add schema tests that verify a 12-character password is accepted, an 11-character password is rejected, a 129-character password is rejected, and unknown JSON fields raise `ValidationError`.

- [ ] **Step 2: Run the tests and confirm they fail for missing M17 contracts.**

Run:

```powershell
E:\anaconda\python.exe -m pytest tests/config/test_auth_settings.py tests/api/test_auth_schemas.py -q
```

Expected: collection or assertion failures stating that the Settings fields or DTOs do not exist. Do not change tests to make this failure disappear.

- [ ] **Step 3: Add the minimal contracts.**

Add `UserRole(str, Enum)` with `USER` and `ADMIN`, and `AuditAction(str, Enum)` with the action names from the design. Add these immutable Settings fields and defaults:

```python
session_ttl_seconds: int = 86400
session_cookie_name: str = "daa_session"
session_cookie_secure: bool = False
auth_admin_emails: tuple[str, ...] = ()
```

Parse `AUTH_SESSION_TTL_SECONDS` as a positive integer, `AUTH_SESSION_COOKIE_NAME` as a nonblank ASCII token, `AUTH_SESSION_COOKIE_SECURE` with strict `true/false` parsing, and `AUTH_ADMIN_EMAILS` as comma-separated lowercased nonblank emails. Force `session_cookie_secure=True` for production and raise `ConfigurationError` if production input explicitly disables it. Add `argon2-cffi>=23.1,<26` to both dependency declarations.

Add strict Pydantic DTOs:

```python
class AuthRegisterRequest(APIModel):
    email: StrictStr
    password: StrictStr


class AuthLoginRequest(APIModel):
    email: StrictStr
    password: StrictStr


class AuthUserResponse(APIModel):
    user_id: UUID
    email: StrictStr
    role: UserRole
    is_active: StrictBool
    created_at: datetime
```

Validate password length in both request models with one shared validator and reject blank email/password values. Do not normalize or hash in the Pydantic layer.

- [ ] **Step 4: Run the focused tests and confirm green.**

Run the same command from Step 2. Expected: all settings, enum, and schema tests pass.

- [ ] **Step 5: Commit the contracts.**

```powershell
git add pyproject.toml requirements.txt src/data_analysis_agent/domain/enums.py src/data_analysis_agent/config/settings.py src/data_analysis_agent/api/schemas.py tests/config/test_auth_settings.py tests/api/test_auth_schemas.py
git commit -m "feat: add authentication configuration contracts"
```

---

### Task 2: 认证持久化模型、仓储和 Alembic 迁移

**Files:**
- Modify: `src/data_analysis_agent/persistence/models.py`, `src/data_analysis_agent/persistence/orm_models.py`, `src/data_analysis_agent/persistence/orm_mappers.py`, `src/data_analysis_agent/persistence/repositories.py`, `src/data_analysis_agent/persistence/unit_of_work.py`
- Create: `alembic/versions/20260929_0006_authentication.py`, `tests/database/test_auth_persistence.py`
- Modify: `tests/database/test_schema.py` to include `auth_sessions` and `audit_events` and the new user columns in the schema contract

**Interfaces:**
- `UserRecord` gains optional `email_normalized`, `password_hash`, `role`, and `is_active` fields.
- `AuthSessionRecord` exposes `session_id`, `token_hash`, `user_id`, `created_at`, `expires_at`, `last_seen_at`, and `revoked_at`.
- `AuditEventRecord` exposes `event_id`, optional `user_id`, `action`, optional `target_type`, optional `target_id`, `success`, `request_id`, `occurred_at`, and `metadata_json`.
- `UserRepository.get_by_email(email_normalized)`, `AuthSessionRepository.get_active_by_token_hash(token_hash, now)`, `AuthSessionRepository.revoke(session_id, revoked_at)`, and `AuditEventRepository.add(record)` are the service-facing methods.

- [ ] **Step 1: Write failing persistence tests.**

Create `tests/database/test_auth_persistence.py` using the existing `uow_factory` fixture. Test these exact behaviors:

```python
def test_user_repository_round_trips_credentials_role_and_active_state(uow_factory):
    now = datetime.now(timezone.utc)
    record = UserRecord(
        email_normalized="owner@example.com",
        password_hash="argon2id$test",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
    )

    with uow_factory() as uow:
        uow.users.ensure(record)
        uow.commit()

    with uow_factory() as uow:
        loaded = uow.users.get_by_email("owner@example.com")

    assert loaded is not None
    assert loaded.user_id == record.user_id
    assert loaded.password_hash == "argon2id$test"
    assert loaded.role is UserRole.ADMIN
    assert loaded.is_active is True


def test_ensure_legacy_user_does_not_overwrite_authentication_fields(uow_factory):
    now = datetime.now(timezone.utc)
    existing = UserRecord(
        email_normalized="owner@example.com",
        password_hash="argon2id$test",
        role=UserRole.ADMIN,
        created_at=now,
    )

    with uow_factory() as uow:
        uow.users.ensure(existing)
        uow.users.ensure(UserRecord(user_id=existing.user_id, created_at=now))
        uow.commit()

    with uow_factory() as uow:
        loaded = uow.users.get(existing.user_id)

    assert loaded.password_hash == "argon2id$test"
    assert loaded.role is UserRole.ADMIN


def test_session_repository_filters_expired_and_revoked_sessions(uow_factory):
    now = datetime.now(timezone.utc)
    active = AuthSessionRecord(
        token_hash="active",
        user_id=uuid4(),
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        last_seen_at=now,
    )
    expired = active.model_copy(update={"session_id": uuid4(), "token_hash": "expired", "expires_at": now - timedelta(seconds=1)})

    with uow_factory() as uow:
        uow.users.ensure(UserRecord(user_id=active.user_id, created_at=now))
        uow.sessions.add(active)
        uow.sessions.add(expired)
        uow.commit()
        assert uow.sessions.get_active_by_token_hash("active", now) == active
        assert uow.sessions.get_active_by_token_hash("expired", now) is None
        assert uow.sessions.revoke(active.session_id, now) is True
        uow.commit()
        assert uow.sessions.get_active_by_token_hash("active", now) is None


def test_audit_repository_persists_failed_login_without_sensitive_metadata(uow_factory):
    event = AuditEventRecord(
        action=AuditAction.LOGIN_FAILED,
        success=False,
        request_id="req-1",
        occurred_at=datetime.now(timezone.utc),
        metadata_json={"email_domain": "example.com"},
    )

    with uow_factory() as uow:
        saved = uow.audit_events.add(event)
        uow.commit()

    assert saved.event_id == event.event_id
```

- [ ] **Step 2: Run the persistence tests and observe the expected RED state.**

```powershell
E:\anaconda\python.exe -m pytest tests/database/test_auth_persistence.py -q
```

Expected: import or attribute failures for the missing records, ORM tables, UoW repositories, and migration schema.

- [ ] **Step 3: Implement records, ORM, mappings, repositories, and UoW wiring.**

Extend `UserRecord` with backward-compatible defaults and map `role` through `UserRole`. Add `SessionORM` and `AuditEventORM` with indexes on `token_hash`, `(user_id, expires_at)`, `(user_id, occurred_at)`, and `(target_type, target_id)`. Add repository methods with SQL filters for `revoked_at IS NULL` and `expires_at > now`; `revoke` must update only an active matching row and return whether one row changed. `UserRepository.ensure` must update only missing legacy fields and never replace a non-null password hash, email, role, or explicit active state.

Create migration `20260929_0006_authentication` after `20260927_0005`:

```python
revision = "20260929_0006"
down_revision = "20260927_0005"
```

Use `batch_alter_table("users")` for SQLite compatibility, add nullable credential columns and non-null defaults for `role`/`is_active`, create a unique index on `email_normalized`, create `auth_sessions` and `audit_events`, and make `downgrade()` drop the new indexes/tables and remove the added columns. Do not make legacy user credentials non-null in the migration.

- [ ] **Step 4: Run focused database tests and migration checks.**

```powershell
E:\anaconda\python.exe -m pytest tests/database/test_auth_persistence.py tests/database/test_schema.py -q
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///./tmp/m17-auth.sqlite3 upgrade head
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///./tmp/m17-auth.sqlite3 upgrade head
```

Expected: persistence tests pass; both Alembic upgrades exit 0 and the second upgrade reports no pending migration.

- [ ] **Step 5: Commit persistence.**

```powershell
git add src/data_analysis_agent/persistence/models.py src/data_analysis_agent/persistence/orm_models.py src/data_analysis_agent/persistence/orm_mappers.py src/data_analysis_agent/persistence/repositories.py src/data_analysis_agent/persistence/unit_of_work.py alembic/versions/20260929_0006_authentication.py tests/database/test_auth_persistence.py tests/database/test_schema.py
git commit -m "feat: add authentication persistence"
```

---

### Task 3: Authentication service and Session lifecycle

**Files:**
- Create: `src/data_analysis_agent/services/auth.py`
- Create: `tests/services/test_auth.py`, `tests/services/test_auth_service.py`

**Interfaces:**
- `PasswordHasher.hash(password: str) -> str` and `PasswordHasher.verify(password: str, encoded_hash: str) -> bool` use Argon2id and convert malformed hashes to `False`.
- `AuthenticatedUser` is an immutable service result containing `user_id`, `email`, `role`, `is_active`, and `created_at`.
- `LoginResult` is an immutable service result containing `user: AuthenticatedUser`, `token: str`, and `expires_at: datetime`.
- `AuthenticationService.register(*, email: str, password: str, request_id: str | None) -> AuthenticatedUser`.
- `AuthenticationService.login(*, email: str, password: str, request_id: str | None) -> LoginResult`.
- `AuthenticationService.authenticate_session(*, token: str, now: datetime | None = None) -> AuthenticatedUser | None`.
- `AuthenticationService.logout(*, token: str, request_id: str | None) -> bool`.

- [ ] **Step 1: Write failing service tests.**

First add this password test to `tests/services/test_auth.py`:

```python
def test_password_hasher_never_returns_the_plaintext_and_rejects_wrong_password():
    from data_analysis_agent.services.auth import PasswordHasher

    encoded = PasswordHasher.hash("correct horse battery staple")

    assert encoded != "correct horse battery staple"
    assert PasswordHasher.verify("correct horse battery staple", encoded) is True
    assert PasswordHasher.verify("wrong password", encoded) is False
```

Then cover these exact service cases in `tests/services/test_auth_service.py`: registration normalizes `Owner@Example.com` to `owner@example.com`; duplicate normalized email raises a stable `EmailAlreadyRegisteredError`; successful login returns a token that is not equal to its stored hash; wrong password raises `InvalidCredentialsError`; disabled users also raise `InvalidCredentialsError`; an active session authenticates; expired and revoked sessions return `None`; logout is idempotent; registration and login success/failure create allowlisted audit events.

Use a real SQLite `UnitOfWork` fixture and the real `PasswordHasher`, not mocks for hashing or the database. Assert no audit metadata contains `password`, `password_hash`, `token`, `cookie`, or the raw login password.

- [ ] **Step 2: Run the service tests and verify a feature-missing failure.**

```powershell
E:\anaconda\python.exe -m pytest tests/services/test_auth_service.py -q
```

Expected: failures for the missing service classes or for unimplemented Session behavior, not fixture errors.

- [ ] **Step 3: Implement the minimal service.**

Use `secrets.token_urlsafe(32)` for the raw Session Token and `hashlib.sha256(token.encode("ascii")).hexdigest()` for `token_hash`. Store `expires_at = utc_now() + timedelta(seconds=settings.session_ttl_seconds)`. Normalize email by stripping and lowercasing; reject values without one nonblank local part and domain with `InvalidCredentialsError` for login or `RequestValidationError` at the API boundary for registration. Use the configured normalized admin email tuple only at registration time to select `UserRole.ADMIN`.

Write auth audit events in the same Unit of Work as the user/session mutation. Use generic failed-login metadata such as `email_domain`, never the raw email local part or password. Keep service exceptions stable and free of database/provider messages.

- [ ] **Step 4: Run service tests and refactor only after green.**

```powershell
E:\anaconda\python.exe -m pytest tests/services/test_auth.py tests/services/test_auth_service.py -q
```

Expected: all password and service tests pass with no unhandled warnings. Refactor duplicate email normalization or audit allowlist code only after the suite is green.

- [ ] **Step 5: Commit the authentication service.**

```powershell
git add src/data_analysis_agent/services/auth.py tests/services/test_auth.py tests/services/test_auth_service.py
git commit -m "feat: add password and session authentication service"
```

---

### Task 4: Session Provider and authentication API

**Files:**
- Modify: `src/data_analysis_agent/api/auth.py`, `src/data_analysis_agent/api/application.py`, `src/data_analysis_agent/api/app.py`, `src/data_analysis_agent/api/schemas.py`, `src/data_analysis_agent/api/routers/__init__.py`
- Create: `src/data_analysis_agent/api/routers/auth.py`, `tests/api/test_auth.py`
- Modify: `tests/api/test_app.py`, `tests/api/test_tasks.py`, `tests/api/test_datasets.py`, `tests/api/test_artifacts.py`

**Interfaces:**
- `Principal` gains `role: UserRole` and `is_admin` property while remaining immutable.
- `SessionPrincipalProvider(auth_service).current_principal(request) -> Principal` reads `Settings.session_cookie_name` and never reads `X-User-ID`.
- `APIApplication.auth_service` is an `AuthenticationService | None`; `from_settings()` creates it when a database is configured.
- Auth routes use the service result and set/clear the configured Cookie without putting the raw token in JSON.

- [ ] **Step 1: Write failing API tests.**

At the top of `tests/api/test_auth.py`, define the real-cookie fixture used below:

```python
@pytest.fixture
def auth_api(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'auth.sqlite3'}",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
        },
    )
    application = APIApplication.from_settings(settings)
    init_database(application.database.engine)
    app = create_app(application)
    try:
        yield SimpleNamespace(
            application=application,
            client=TestClient(app),
            unauthenticated=TestClient(app),
        )
    finally:
        application.database.engine.dispose()
```

Import `SimpleNamespace`, `pytest`, `TestClient`, `create_app`, `APIApplication`, `load_settings`, and `init_database` in that file. Create a separate `production_test_settings()` helper in the same file with the following complete implementation:

```python
def production_test_settings():
    return load_settings(
        app_env="production",
        environ={
            "APP_ENV": "production",
            "OPENAI_API_KEY": "key",
            "OPENAI_BASE_URL": "https://llm.example",
            "OPENAI_MODEL": "model",
            "DATABASE_URL": "mysql+pymysql://user:pass@db/app",
            "STORAGE_ENDPOINT": "https://s3.example",
            "STORAGE_BUCKET": "bucket",
            "EXECUTION_IMAGE": "analysis:latest",
        },
    )
```

Create the test application with SQLite and assert:

```python
def test_register_login_me_and_logout_invalidate_the_session(auth_api):
    client = auth_api.client
    registered = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    )
    assert registered.status_code == 201

    login = client.post(
        "/api/auth/login",
        json={"email": "OWNER@example.com", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200
    assert "daa_session=" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert "correct horse battery staple" not in login.text
    assert "token" not in login.json()

    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_default_app_does_not_accept_a_user_id_header(auth_api):
    response = auth_api.unauthenticated.get(
        "/api/datasets", headers={"X-User-ID": str(uuid4())}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_production_rejects_header_principal_provider():
    settings = production_test_settings()
    application = APIApplication(settings=settings, principal_provider=HeaderPrincipalProvider())
    with pytest.raises(ConfigurationError, match="Header"):
        create_app(application)
```

Also test duplicate email (`409`), wrong password (`401 INVALID_CREDENTIALS`), malformed registration (`422 REQUEST_VALIDATION_ERROR`), disabled account (`401`), and logout without a cookie (`204`).

- [ ] **Step 2: Run the tests and confirm they fail before API implementation.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_auth.py -q
```

Expected: missing router/service/provider failures or 404/401 behavior inconsistent with the new contract.

- [ ] **Step 3: Implement Provider, application wiring, and routes.**

In `create_app`, when no Provider is explicitly injected, install `SessionPrincipalProvider` if an auth service exists. Reject `HeaderPrincipalProvider` in production. Do not install Header Provider automatically for development or test. Update the existing API fixtures in `test_app.py`, `test_tasks.py`, `test_datasets.py`, and `test_artifacts.py` in this task so they explicitly assign `HeaderPrincipalProvider()` before calling `create_app(application)`; update assertions in `test_app.py` that previously expected automatic Header Provider installation.

Register `auth_router` under `/api`. `register` returns `AuthUserResponse` with status 201. `login` calls `AuthenticationService.login`, sets the Cookie with `httponly=True`, `samesite="lax"`, `secure=settings.session_cookie_secure`, `max_age=settings.session_ttl_seconds`, and returns `AuthUserResponse`. `logout` calls `logout`, deletes the Cookie with the same name/path, and returns 204. `me` depends on `get_current_principal`, loads the public user projection, and returns it.

Map service exceptions to existing `APIError` codes without passing exception strings to clients. Preserve the request ID middleware and `ErrorResponse` shape.

- [ ] **Step 4: Run focused auth API tests and app foundation tests.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_auth.py tests/api/test_auth_schemas.py tests/api/test_api_foundation.py tests/api/test_app.py -q
```

Expected: all new auth flow and app boundary tests pass, and existing API fixtures use only their explicit test Provider.

- [ ] **Step 5: Commit the API authentication boundary.**

```powershell
git add src/data_analysis_agent/api/auth.py src/data_analysis_agent/api/application.py src/data_analysis_agent/api/app.py src/data_analysis_agent/api/schemas.py src/data_analysis_agent/api/routers/auth.py src/data_analysis_agent/api/routers/__init__.py tests/api/test_auth.py tests/api/test_app.py tests/api/test_tasks.py tests/api/test_datasets.py tests/api/test_artifacts.py
git commit -m "feat: add session authentication API"
```

---

### Task 5: Central authorization and owner-scoped resource operations

**Files:**
- Create: `src/data_analysis_agent/services/authorization.py`, `tests/api/test_authorization.py`
- Modify: `src/data_analysis_agent/services/persistence.py`, `src/data_analysis_agent/datasets/service.py`, `src/data_analysis_agent/persistence/repositories.py`, `src/data_analysis_agent/api/routers/tasks.py`, `src/data_analysis_agent/api/routers/datasets.py`

**Interfaces:**
- `AccessSubject(user_id: UUID, role: UserRole)` is the service-layer authorization value object; `AccessSubject.is_admin` is true only for `ADMIN`.
- `AuthorizationService.can_access_owner(subject, owner_id) -> bool` and `AuthorizationService.require_admin(subject) -> None` are the only role decisions used by routes/services.
- Existing UUID-based Worker and compatibility methods remain available; add Principal-aware methods rather than changing Worker signatures.

- [ ] **Step 1: Write failing cross-user and administrator tests.**

Using two registered users and one seeded admin, add tests that:

1. User A uploads a dataset and creates a task.
2. User B gets `404 DATASET_NOT_FOUND` for A's dataset, `404 TASK_NOT_FOUND` for A's task, and cannot cancel or retry it.
3. User B cannot create a task referencing A's dataset.
4. The admin can list/get A's dataset and task, and can access existing task events/actions.
5. All unknown IDs produce the same 404 code and response shape as foreign IDs.
6. User-specific idempotency keys remain independent.

Use real authenticated `TestClient` cookies obtained through the auth API. Do not send `X-User-ID` in these tests.

- [ ] **Step 2: Run authorization tests and observe RED.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_authorization.py -q
```

Expected: administrator access is denied or the existing owner-only methods expose an incorrect status, demonstrating the missing Principal-aware behavior.

- [ ] **Step 3: Implement the centralized subject and repository scopes.**

Add `AccessSubject` and `AuthorizationService`. Extend repositories with explicit methods such as `get_for_subject`, `list_for_subject`, and `get_owner_id`; the implementation must use an `if subject.is_admin` branch that calls an all-owner query or a `WHERE user_id = subject.user_id` query. Do not represent administrator scope with a nullable user ID that could be accidentally omitted.

Add Principal-aware methods to `DatasetCatalogService` and `TaskPersistenceService` for list/detail/events/artifacts/cancel/retry. Keep `get_for_user` and UUID worker methods intact for existing callers. When a task references datasets, validate every dataset through the subject-aware dataset repository before attaching it.

Map `EntityNotFoundError` to the route's existing stable not-found API error. Keep this task focused on authorization results; Task 7 adds the audit writer and records these resource actions after the owner/role behavior is green.

- [ ] **Step 4: Run focused authorization and existing resource API tests.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_authorization.py tests/api/test_tasks.py tests/api/test_datasets.py -q
```

Expected: new cross-user/admin tests and existing owner-isolation tests all pass.

- [ ] **Step 5: Commit resource authorization.**

```powershell
git add src/data_analysis_agent/services/authorization.py src/data_analysis_agent/services/persistence.py src/data_analysis_agent/datasets/service.py src/data_analysis_agent/persistence/repositories.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/routers/datasets.py tests/api/test_authorization.py
git commit -m "feat: enforce principal resource authorization"
```

---

### Task 6: Artifact/report download authorization

**Files:**
- Modify: `src/data_analysis_agent/storage/access.py`, `src/data_analysis_agent/api/application.py`, `src/data_analysis_agent/api/routers/artifacts.py`, `src/data_analysis_agent/persistence/repositories.py`
- Modify: `tests/api/test_artifacts.py`

**Interfaces:**
- Existing UUID compatibility methods remain available for storage unit tests: `create_download_url(artifact_id, user_id, expires_in) -> str` and `open_download(artifact_id, user_id, download_url) -> tuple[ArtifactRecord, BinaryIO]`.
- New API methods are `create_download_url_for_subject(artifact_id, subject, expires_in) -> str` and `open_download_for_subject(artifact_id, subject, download_url) -> tuple[ArtifactRecord, BinaryIO]`; both check subject scope before continuing.
- `AuthorizedArtifactLookup.get_for_subject(artifact_id, subject) -> ArtifactRecord | None` resolves owner through the task relationship and supports admin scope.

- [ ] **Step 1: Write failing download tests.**

Extend `tests/api/test_artifacts.py` with these cases:

```python
def test_foreign_user_cannot_use_a_download_url_or_content_url(artifact_api):
    application, artifact, owner, foreign = artifact_api
    metadata = owner.get(f"/api/artifacts/{artifact.artifact_id}")
    assert metadata.status_code == 200

    foreign_metadata = foreign.get(f"/api/artifacts/{artifact.artifact_id}")
    foreign_content = foreign.get(metadata.json()["content_url"])

    assert foreign_metadata.status_code == 404
    assert foreign_content.status_code == 404
    assert foreign_content.json()["code"] == "ARTIFACT_NOT_FOUND"


```

Also test tampered/expired URLs, changed file hash, no raw `file_path`/`storage_uri` in responses, and an admin download of another user's artifact. Audit assertions belong to Task 7 after `AuditWriter` exists.

- [ ] **Step 2: Run artifact tests and confirm the missing admin/double-check behavior.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_artifacts.py -q
```

Expected: foreign content access or admin access fails until FileAccessService accepts an authorized subject.

- [ ] **Step 3: Implement subject-aware file access and protected content URLs.**

Change the API route's internal lookup to use `get_for_subject`, `create_download_url_for_subject`, and `open_download_for_subject`. For local storage preserve the existing opaque `local-download://` contract; for remote storage return an application-protected content URL instead of exposing a raw object-store URL. The content endpoint must call the subject-aware method before reading bytes. Map every `FileAccessDeniedError` to the existing 404 contract. Keep the existing UUID methods as owner-only compatibility wrappers for non-API callers; they must not be used by API routes.

- [ ] **Step 4: Run storage and artifact regression tests.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_artifacts.py tests/storage/test_access.py tests/storage/test_contracts.py -q
```

Expected: all download permission, signature, and file integrity tests pass.

- [ ] **Step 5: Commit protected downloads.**

```powershell
git add src/data_analysis_agent/storage/access.py src/data_analysis_agent/api/application.py src/data_analysis_agent/api/routers/artifacts.py src/data_analysis_agent/persistence/repositories.py tests/api/test_artifacts.py
git commit -m "feat: protect artifact downloads with authorization"
```

---

### Task 7: Audit integration and configuration docs

**Files:**
- Create: `src/data_analysis_agent/services/audit.py`, `tests/api/test_audit.py`
- Modify: `src/data_analysis_agent/api/app.py`, `src/data_analysis_agent/api/application.py`, `src/data_analysis_agent/api/routers/tasks.py`, `src/data_analysis_agent/api/routers/datasets.py`, `src/data_analysis_agent/api/routers/artifacts.py`, `src/data_analysis_agent/services/auth.py`, `tests/database/conftest.py`
- Modify: `.env.development.example`, `.env.test.example`, `.env.production.example`, `README.md`

**Interfaces:**
- `AuditWriter.record(*, action: AuditAction, user_id: UUID | None, request_id: str, target_type: str | None = None, target_id: UUID | None = None, success: bool, metadata: Mapping[str, str | int | bool] | None = None) -> AuditEventRecord` writes only allowlisted metadata.
- Existing API test fixtures explicitly set `application.principal_provider = HeaderPrincipalProvider()` before `create_app(application)`; this is a test adapter, not an automatic runtime behavior.

- [ ] **Step 1: Write failing audit integration tests.**

Add tests that register/login/logout, upload/delete a dataset, create/cancel/retry a task, attempt a denied foreign access, and download a report. Assert one audit event for each action, the correct `user_id` when known, the request ID, and absence of these strings in serialized event metadata: `password`, `password_hash`, `daa_session`, `token`, `file_path`, `source_uri`, `storage_uri`, `DATABASE_URL`.

- [ ] **Step 2: Run audit integration tests and confirm missing events.**

```powershell
E:\anaconda\python.exe -m pytest tests/api/test_audit.py tests/api/test_auth.py -q
```

Expected: auth flow may pass, but business-action audit assertions fail until route integration is complete.

- [ ] **Step 3: Implement the audit writer and route integration.**

Add `AuditWriter` in `src/data_analysis_agent/services/audit.py` using `UnitOfWork.audit_events.add()`. Allow only scalar metadata keys `email_domain`, `resource_type`, `role`, `status_code`, and `reason_code`; discard or reject all other keys. Pass `request.state.request_id` from routes. For mutation actions, write the audit row in the same Unit of Work as the business mutation. For denied reads/downloads, write a best-effort event with no sensitive details and preserve the stable public error.

Wire `AuditWriter` into `APIApplication` and the auth/resource routes. Update environment examples with `AUTH_SESSION_TTL_SECONDS`, `AUTH_SESSION_COOKIE_NAME`, `AUTH_SESSION_COOKIE_SECURE`, and `AUTH_ADMIN_EMAILS`; document that Header authentication is test-only and show the register/login flow in README.

- [ ] **Step 4: Run all API and persistence regression tests.**

```powershell
E:\anaconda\python.exe -m pytest tests/api tests/database -q
```

Expected: all API/database tests pass, including old owner isolation tests and new session tests.

- [ ] **Step 5: Commit audit integration and documentation.**

```powershell
git add src/data_analysis_agent/services/audit.py src/data_analysis_agent/api/app.py src/data_analysis_agent/api/application.py src/data_analysis_agent/api/routers/tasks.py src/data_analysis_agent/api/routers/datasets.py src/data_analysis_agent/api/routers/artifacts.py src/data_analysis_agent/services/auth.py tests/api/test_audit.py tests/database/conftest.py .env.development.example .env.test.example .env.production.example README.md
git commit -m "feat: audit authenticated API operations"
```

---

### Task 8: Full verification and delivery review

**Files:**
- Modify only if verification exposes a concrete regression: the smallest affected source/test file.
- Do not modify the protected design document or unrelated untracked `worktrees/`.

**Interfaces:**
- All M17 public behavior is covered by the committed tests and the design document at `docs/superpowers/specs/2026-09-29-user-authentication-and-authorization-design.md`.

- [ ] **Step 1: Run the complete M17 focused suite.**

```powershell
E:\anaconda\python.exe -m pytest tests/services/test_auth.py tests/services/test_auth_service.py tests/config/test_auth_settings.py tests/api/test_auth_schemas.py tests/api/test_auth.py tests/api/test_authorization.py tests/api/test_audit.py tests/api/test_artifacts.py tests/database/test_auth_persistence.py -q
```

Expected: all M17 tests pass with no new warnings or errors.

- [ ] **Step 2: Run the existing complete suite.**

```powershell
E:\anaconda\python.exe -m pytest -q
```

Expected: the pre-M17 baseline remains green except for already documented skips; any new failure is fixed before completion.

- [ ] **Step 3: Verify static compilation, migration idempotency, and diff hygiene.**

```powershell
E:\anaconda\python.exe -m compileall -q src
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///./tmp/m17-final.sqlite3 upgrade head
E:\anaconda\python.exe -m alembic -x db_url=sqlite:///./tmp/m17-final.sqlite3 upgrade head
git diff --check
```

Expected: all commands exit 0; the second migration run is a no-op; no whitespace errors are reported.

- [ ] **Step 4: Perform the acceptance checklist.**

Verify with real TestClient flows that: an unauthenticated request cannot list business data; a second user cannot access another user's dataset/task/artifact/report by replacing an ID; a foreign download URL fails at content time; logout invalidates the old Session; an admin crosses the owner boundary while a regular user does not; password hashes and Session Tokens never appear in API responses or audit records; and the production app rejects Header Provider configuration.

- [ ] **Step 5: Commit only concrete verification repairs and report results.**

If Step 1-4 required repairs, run the affected focused tests again and commit them with a specific message such as `fix: close M17 session authorization regression`. Otherwise leave the tree unchanged apart from the M17 commits and report the exact test/compile/migration results.
