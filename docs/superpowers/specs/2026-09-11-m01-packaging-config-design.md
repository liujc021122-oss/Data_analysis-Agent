# M01 项目打包与配置管理设计

日期：2026-09-11  
状态：已获用户确认，待编写实现计划

## 目标

把当前依赖项目根目录运行的 Python 示例整理为可安装的 `data-analysis-agent` 应用：生产代码只有一个规范包来源，根目录入口继续兼容旧用法；配置由类型化对象统一加载，开发、测试和生产环境彼此隔离；`quick_analysis` 在所有入口保持同一签名。

## 范围与非目标

本阶段包含：

- 新增 `pyproject.toml`，使用 setuptools 构建，支持 `pip install -e .`。
- 将生产代码迁移到 `src/data_analysis_agent/`，包导入名统一为 `data_analysis_agent`。
- 保留根目录 `data_analysis_agent.py`、`main.py`、`config/`、`utils/` 等兼容入口，但它们只做转发，不保留第二份业务实现。
- 增加开发、测试、生产环境的类型化配置加载与校验。
- 分离生产依赖和开发依赖，统一 Python 版本下限为 `>=3.10`。
- 统一日志级别、输出目录、模型配置和资源限制配置。
- 修复所有公共 `quick_analysis` 入口的参数差异。

本阶段不包含：

- 数据库、Redis、对象存储客户端的实际接入。
- 任务超时执行器、上传服务或新的安全沙箱。
- Agent 分析行为、执行器行为和报告格式的功能重构。
- 删除根目录兼容模块。

## 包结构

规范生产包结构如下：

```text
pyproject.toml
src/
└── data_analysis_agent/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py
    ├── config/
    │   ├── __init__.py
    │   ├── llm.py
    │   └── settings.py
    ├── agent/
    │   ├── __init__.py
    │   ├── core.py
    │   └── prompts.py
    ├── execution/
    │   ├── __init__.py
    │   └── code_executor.py
    ├── reports/
    │   ├── __init__.py
    │   └── word.py
    └── services/
        ├── __init__.py
        ├── llm.py
        ├── openai_client.py
        ├── responses.py
        └── session.py
```

职责边界：

- `config` 只负责配置对象、环境变量和配置错误。
- `agent` 只负责 Agent 编排、提示词和公共分析 API。
- `execution` 只负责现有 IPython/Matplotlib 代码执行行为。
- `reports` 只负责 Markdown 到 Word 的报告转换。
- `services` 负责 LLM 客户端、响应解析/格式化和会话目录。
- 包内部只使用包内相对导入；不再在新生产代码中混用根目录绝对导入。

## 根目录兼容策略

根目录入口继续存在，但不再有第二份实现：

- `data_analysis_agent.py` 是兼容桥接模块，加载 `src/data_analysis_agent` 的同一套实现并导出相同的 `DataAnalysisAgent`、`LLMConfig`、`Settings` 和 `quick_analysis`。
- 由于根目录文件与安装包同名，桥接模块必须在加载源包前设置源包搜索路径或通过 importlib 加载源包，不能在桥接模块中直接执行 `from data_analysis_agent import ...`，避免递归导入。
- 根目录 `config/`、`utils/` 和 `prompts.py` 改为旧路径转发模块，以兼容现有调用方和 M00 测试；其实现来源全部指向 `data_analysis_agent.*`。
- 根目录 `main.py` 转发到 `data_analysis_agent.cli.main`。
- 从项目根目录导入时，兼容桥接最终使用 `src` 下的实现；执行 `pip install -e .` 后从项目外导入时，直接使用安装包。新代码和新测试只使用 `data_analysis_agent.*` 规范路径。

## 公共 API

`src/data_analysis_agent/__init__.py` 导出：

```python
from data_analysis_agent import (
    ConfigurationError,
    DataAnalysisAgent,
    LLMConfig,
    Settings,
    quick_analysis,
)
```

规范 `quick_analysis` 签名为：

```python
def quick_analysis(
    query: str,
    files: Sequence[str] | None = None,
    *,
    output_dir: str | Path | None = None,
    max_rounds: int | None = None,
    generate_word_report: bool | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    raise NotImplementedError
```

`query` 是唯一的用户需求参数名；`DataAnalysisAgent.analyze()` 继续使用现有的 `user_input` 参数，以保持 M00 固定的行为契约。根目录兼容入口和安装后的规范包必须导出同一个实现与同一签名。

## 配置模型

### 类型化对象

`config/llm.py` 定义冻结 dataclass：

```python
@dataclass(frozen=True)
class LLMConfig:
    api_key: str | None
    base_url: str
    model: str
    temperature: float
    max_tokens: int
```

`config/settings.py` 定义：

```python
EnvironmentName = Literal["development", "test", "production"]

@dataclass(frozen=True)
class Settings:
    app_env: EnvironmentName
    database_url: str | None
    redis_url: str | None
    storage_endpoint: str | None
    storage_bucket: str | None
    openai_api_key: str | None
    openai_base_url: str
    openai_model: str
    max_task_runtime: int
    max_upload_size: int
    output_dir: Path
    log_level: str

    def llm_config(self) -> LLMConfig:
        raise NotImplementedError
```

`Settings.llm_config()` 是 Agent 使用 LLM 配置的唯一转换点；业务模块不直接读取 `os.environ`。

### 加载优先级与环境隔离

提供：

```python
def load_settings(
    app_env: EnvironmentName | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_dir: Path | None = None,
    output_dir: str | Path | None = None,
) -> Settings:
    raise NotImplementedError
```

加载规则固定如下：

1. `APP_ENV` 决定环境；未指定时默认为 `development`。
2. 只加载所选环境的 `.env.development`、`.env.test` 或 `.env.production`。
3. 进程环境变量优先于 dotenv 文件；显式 `environ` 用于测试并优先于文件。
4. 只有 `development` 在对应文件缺失时允许回退读取普通 `.env`；`test` 和 `production` 不读取普通 `.env`，避免测试或生产被本地配置污染。
5. `test` 环境允许没有 `OPENAI_API_KEY`，并由测试注入 fake LLM；不会创建真实 OpenAI 客户端或发起网络请求。
6. `production` 环境缺少 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 或 `OPENAI_MODEL` 时抛出 `ConfigurationError`，错误文本明确列出字段名，但绝不输出密钥值。
7. `MAX_TASK_RUNTIME` 和 `MAX_UPLOAD_SIZE` 必须是正整数；非法 `APP_ENV`、`LOG_LEVEL` 或数值配置同样抛出 `ConfigurationError`。

支持的配置键：

```text
APP_ENV
DATABASE_URL
REDIS_URL
STORAGE_ENDPOINT
STORAGE_BUCKET
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
MAX_TASK_RUNTIME
MAX_UPLOAD_SIZE
OUTPUT_DIR
LOG_LEVEL
```

默认值固定为：开发环境输出到 `outputs/`、测试环境输出到 `outputs/test/`、生产环境输出到 `outputs/production/`；`MAX_TASK_RUNTIME=900` 秒、`MAX_UPLOAD_SIZE=104857600` 字节、`LOG_LEVEL=INFO`。这些限制在 M01 中完成建模和校验，不在本阶段实现超时或上传行为。

提供 `.env.development.example`、`.env.test.example` 和 `.env.production.example` 作为无秘密模板。模板不包含 API Key；真实 `.env*` 文件继续由 `.gitignore` 排除。

### 日志

包级 `logging` 初始化函数读取 `Settings.log_level`，统一配置 logger 级别和格式。诊断信息使用 logger，不在新包中直接使用 `print`；现有用户可见分析输出行为由 M00 契约保护，迁移时不改变返回值和错误语义。日志过滤和异常文本不得包含 `OPENAI_API_KEY` 的实际值。

## 依赖与构建

`pyproject.toml` 使用 `setuptools.build_meta`，声明：

- distribution name：`data-analysis-agent`
- import package：`data_analysis_agent`
- Python：`>=3.10`
- console script：`data-analysis-agent = data_analysis_agent.cli:main`
- module entry：`python -m data_analysis_agent`

生产依赖只包含运行时所需包：pandas、numpy、matplotlib、duckdb、scipy、scikit-learn、plotly、openai、PyYAML、python-dotenv、python-docx 和 IPython。开发依赖通过 `.[dev]` extra 提供：pytest、pytest-asyncio、black、flake8。

根目录 `requirements.txt` 暂时保留为兼容安装文件，并与 `pyproject.toml` 的生产/开发依赖版本下限保持一致；不再把开发工具当作生产依赖安装。

## CLI

`data_analysis_agent.cli.main()` 是唯一 CLI 实现：

- `python -m data_analysis_agent` 调用 `data_analysis_agent.__main__`，再转发到 `cli.main()`。
- `data-analysis-agent` console script 调用同一个 `cli.main()`。
- 根目录 `main.py` 只转发到同一个 `cli.main()`。

CLI 使用 `Settings` 和规范 `quick_analysis`，不在入口中重复读取 API 配置。无配置或文件错误时返回非零退出码并显示字段/文件名等可定位信息。

## 迁移与行为保持

迁移采用“先复制到 src、再让旧路径转发、最后删除重复实现”的顺序。每次迁移后运行 M00 的 31 项测试；如果导入路径迁移造成行为差异，先增加回归测试并修复兼容层，不修改既有业务契约。特别保持：

- Agent 正常执行、失败反馈、空/非法响应和最大轮数行为。
- 图表生成、Markdown/Word 报告及 Word 失败保留 Markdown 的返回字段。
- 会话目录和图片路径边界行为。
- `quick_analysis` 返回结构和 fake LLM 离线流程。

## 验证方案

新增 M01 测试分组：

1. `tests/packaging/`：解析 `pyproject.toml`、editable install、项目外导入、`python -m data_analysis_agent` 和 console script。
2. `tests/config/`：三环境加载、优先级、dotenv 隔离、类型转换、缺失字段错误、密钥不进日志。
3. `tests/public_api/`：规范包导出、根目录兼容导出和 `quick_analysis` 签名一致。
4. M00 全量回归：不设置 API Key 时所有 fake LLM 测试通过，不发起网络请求，现有 31 项行为契约保持通过。

验收命令：

```powershell
\.venv\Scripts\python.exe -m pip install -e .
\.venv\Scripts\python.exe -m pytest -q
\.venv\Scripts\python.exe -m compileall -q src tests
```

同时从项目外临时目录执行：

```powershell
python -c "import data_analysis_agent; print(data_analysis_agent.__file__)"
python -m data_analysis_agent --help
data-analysis-agent --help
```

验收必须证明：editable install 成功、规范包可以模块方式启动、公共导入路径统一、生产缺失配置给出明确错误、测试环境不读取普通 `.env` 或生产配置、M00 行为测试继续通过。
