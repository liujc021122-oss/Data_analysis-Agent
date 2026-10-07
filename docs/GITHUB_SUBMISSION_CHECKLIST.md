# GitHub 提交检查单

这份检查单用于把当前项目安全地提交到 GitHub。仓库当前可能同时存在功能开发、测试和本次 README 变更；请先审阅 `git status`，不要直接执行 `git add .` 或把整个工作目录当作提交边界。

## 1. 提交前检查

在仓库根目录执行：

```powershell
git branch --show-current
git status --short
git remote -v
git log -5 --oneline --decorate
```

确认以下内容不会进入提交：

- `.env`、`.env.development`、`.env.test`、`.env.production`、`.env.compose`
- 数据库文件、`outputs/`、`tmp/`、`.playwright-cli/`、`.venv/`
- 本地证书、签名密钥、API Key、密码和运行日志
- `worktrees/`、`.worktrees/` 以及其他嵌套 checkout
- 尚未完成或不属于这次发布的源代码和测试改动

检查忽略规则：

```powershell
git check-ignore -v .env .env.development .env.test .env.production .env.compose outputs tmp .playwright-cli .venv data_analysis_agent.sqlite3
```

扫描常见私密材料。命令没有输出才可以继续；若有命中，先人工确认并从提交范围移除：

```powershell
rg -n --hidden -g '!.git/**' -g '!outputs/**' -g '!tmp/**' -g '!frontend/node_modules/**' "sk-[A-Za-z0-9]|AIza[0-9A-Za-z_-]{20,}|-----BEGIN (RSA|OPENSSH|EC|PRIVATE) KEY-----|password\s*[:=]\s*[^<\s]+" .
```

## 2. 本地验证

后端：

```powershell
python -m pytest -q
python -m compileall -q src
```

前端：

```powershell
Push-Location frontend
npm ci
npm run typecheck
npm run test:run
npm run build
Pop-Location
```

部署契约和文档资产：

```powershell
python -m pytest tests/deployment/test_contract.py tests/packaging/test_dependency_constraints.py -q
git diff --check
```

Docker Desktop 不可用时，不要把 Compose 运行时、镜像构建、网络、迁移和持久化重启写成已在本机验证；请按 [本地 Compose 部署指南](deployment/local-compose.md) 在 Docker-enabled CI 或其他 Docker 主机验证。

## 3. 只提交本次 README 材料

适合只发布 README、截图和提交规范时使用。先确认 `git status` 中没有已经暂存的无关文件，再执行：

```powershell
git add -- README.md docs/GITHUB_SUBMISSION_CHECKLIST.md
git add -f -- docs/images/report-preview.png docs/images/chart-preview.png

git diff --cached --stat
git diff --cached --check
git diff --cached
```

检查 staged 文件列表必须只包含以下四个路径：

```powershell
git diff --cached --name-only
```

- `README.md`
- `docs/GITHUB_SUBMISSION_CHECKLIST.md`
- `docs/images/report-preview.png`
- `docs/images/chart-preview.png`

确认无误后提交：

```powershell
git commit -m "docs: prepare GitHub project documentation"
```

## 4. 首次上传整个项目

如果 `git status` 中的源代码、测试、部署和配置改动都已经完成审阅，并且本次确实要一起上传，使用以下显式 allowlist。它会包含项目源代码和测试，但不会包含环境文件、运行输出、临时目录、根目录数据集或嵌套 worktree：

```powershell
git add -- `
  .github `
  alembic `
  assets `
  config `
  deploy `
  docs `
  frontend `
  requirements `
  scripts `
  src `
  tests `
  .dockerignore `
  .gitignore `
  Dockerfile `
  LICENSE `
  README.md `
  alembic.ini `
  compose.yaml `
  compose.https.yaml `
  mypy.ini `
  pyproject.toml `
  requirements.txt `
  requirements-dev.txt `
  main.py `
  data_analysis_agent.py `
  prompts.py `
  utils

git add -f -- docs/images/report-preview.png docs/images/chart-preview.png
git status --short
git diff --cached --stat
git diff --cached --check
git diff --cached
```

不要把以下未跟踪文件通过 `git add -A` 一并加入：

- `cpc.csv`、`shop.csv` 或其他本地业务数据
- `outputs/`、`tmp/`、`.playwright-cli/`
- `task_plan.md`、`findings.md`、`progress.md`
- `worktrees/` 和 `.worktrees/`
- `.env*`、本地数据库、证书和日志

确认 staged diff 只包含本次要发布的项目文件后提交：

```powershell
git commit -m "feat: publish data analysis agent platform"
```

## 5. 配置 GitHub remote 并推送

先检查是否已经存在 `origin`：

```powershell
git remote -v
```

如果没有输出，在 GitHub 创建一个空仓库后，将 GitHub 页面提供的 HTTPS 或 SSH 地址填入下面命令；不要保留示例地址：

```powershell
git remote add origin https://github.com/ACCOUNT/REPOSITORY.git
```

确认远程地址正确：

```powershell
git remote -v
git branch --show-current
```

当前项目默认分支为 `main` 时推送：

```powershell
git push -u origin main
```

如果远程仓库已经有 README、License 或其他初始 commit，不要直接覆盖；先拉取并处理历史合并：

```powershell
git pull --rebase origin main
git push -u origin main
```

本检查单不会代替用户执行 `git remote add`、`git pull` 或 `git push`，也不会处理 GitHub 认证、分支保护或远程冲突。

## 6. 暂存回退

如果发现 staged 内容不正确：

```powershell
git restore --staged README.md
git restore --staged docs/GITHUB_SUBMISSION_CHECKLIST.md
git restore --staged docs/images/report-preview.png
git restore --staged docs/images/chart-preview.png
git status --short
```

`git restore --staged` 只取消暂存，不会回滚工作区文件。需要放弃工作区修改前，先确认文件确实属于本次任务，并使用可恢复的备份或版本控制操作。

## 7. GitHub 页面确认

推送完成后，在 GitHub 页面检查：

- README 顶部两张图片正常显示。
- Mermaid 架构图和任务生命周期正常渲染。
- `docs/deployment/local-compose.md`、`docs/GITHUB_SUBMISSION_CHECKLIST.md` 和 `LICENSE` 链接可点击。
- 仓库文件列表中没有 `.env`、数据库、输出目录、真实数据或密钥。
- Actions 页面中的 backend、frontend、deployment 和 integration checks 按预期运行。
