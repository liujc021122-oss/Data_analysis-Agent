# 数据分析智能体 (Data Analysis Agent)

🤖 **基于 LLM 的智能数据分析代理**

## 📋 项目简介

数据分析智能体是一个 Python 工具，它结合了大语言模型（LLM）的理解能力与 Python 数据分析库的计算能力，将「自然语言需求」直接转化为「可执行的分析代码 + 可视化图表 + 结构化报告」。你只需用一句话描述想分析什么，剩下的交给智能体。

- 🎯 **自然语言分析**：接受用户的自然语言需求，自动生成专业的数据分析代码
- 📊 **智能可视化**：自动生成高质量的图表，支持中文显示，输出到专用会话目录
- 🔄 **多轮迭代优化**：基于执行结果自动调整分析策略，持续优化分析质量
- 📝 **双格式报告**：自动生成包含图表和分析结论的专业报告（Markdown + Word）
- 🛡️ **安全执行**：生产使用一次性受限容器；IPython 仅保留给开发和测试兼容路径
- 🔧 **错误自愈**：自动检测并修复常见错误（编码、列名、数据类型等）

## ✨ 核心特性

### 🧠 智能分析流程

- **多阶段分析**：数据探索 → 清洗检查 → 分析可视化 → 图片收集 → 报告生成
- **错误自愈**：自动检测并修复常见错误（编码、列名、数据类型等）
- **上下文保持**：Notebook 环境中变量和状态在分析过程中持续保持

### 📋 多格式报告

- **Markdown 报告**：结构化的分析报告，包含图表引用
- **Word 文档**：将标题、段落、列表和图表转换为可分享、可打印的 `.docx`
- **分析摘要样式**：识别 `【部分总结】` 与 `【分析要点】` 语义标记，总结渲染为橙色重点段、要点渲染为浅灰斜体
- **统一标题样式**：各级标题统一使用 Microsoft YaHei 字体，并按层级控制字号
- **图片集成**：将会话目录中的 PNG 图表以带浅灰边框和柔和阴影的内容板块嵌入 Word 文档
- **故障兜底**：Word 转换失败时仍保留 Markdown，并在返回值中提供错误信息

## 🏗️ 项目架构

```
data-analysis-agent/
├── 📁 assets/images/              # README 展示截图
├── 📁 config/
│   ├── __init__.py
│   └── llm_config.py              # LLM 配置（API Key、模型、温度、max_tokens）
├── 📁 utils/
│   ├── __init__.py
│   ├── code_executor.py           # 安全的代码执行器（基于 IPython，AST 安全检查）
│   ├── llm_helper.py              # LLM 调用辅助（同步/异步、YAML 响应解析）
│   ├── fallback_openai_client.py  # OpenAI 兼容客户端（支持故障转移）
│   ├── extract_code.py            # 从 LLM 响应中提取代码
│   ├── format_execution_result.py # 代码执行结果格式化
│   ├── create_session_dir.py      # 会话输出目录创建
│   └── word_report_generator.py   # Markdown → Word 报告生成
├── 📁 tests/                      # 单元测试
├── 📄 __init__.py                 # 包初始化
├── 📄 data_analysis_agent.py      # 主智能体实现
├── 📄 prompts.py                  # 系统提示词模板
├── 📄 main.py                     # 命令行入口 / 使用示例
├── 📄 requirements.txt            # 项目依赖
├── 📄 .env.example                # 环境变量模板
├── 📄 .gitignore
├── 📄 LICENSE                     # MIT 许可证
└── 📁 outputs/                    # 分析结果输出目录（自动生成）
    └── session_[32位UUID]/        # 每次分析的独立会话目录
        ├── *.png                  # 生成的图表
        ├── 最终分析报告.md        # Markdown 报告
        └── 最终分析报告.docx      # Word 报告（默认生成）
```

## 📊 工作流程

使用 Mermaid 图表展示完整的数据分析流程（GitHub 可直接渲染）：

```mermaid
graph TD
    A[用户输入自然语言需求] --> B[初始化智能体]
    B --> C[创建专用会话目录]
    C --> D[LLM理解需求并生成代码]
    D --> E[安全代码执行器执行]
    E --> F{执行是否成功?}
    F -->|失败| G[错误分析与修复]
    G --> D
    F -->|成功| H[结果格式化与存储]
    H --> I{是否需要更多分析?}
    I -->|是| J[基于当前结果继续分析]
    J --> D
    I -->|否| K[收集所有图表]
    K --> L[生成最终分析报告]
    L --> M[输出Markdown和Word报告]
    M --> N[分析完成]

    style A fill:#e1f5fe
    style N fill:#c8e6c9
    style F fill:#fff3e0
    style I fill:#fff3e0
```

```mermaid
sequenceDiagram
    participant User as 用户
    participant Agent as 数据分析智能体
    participant LLM as 语言模型
    participant Executor as 代码执行器
    participant Storage as 文件存储

    User->>Agent: 提供数据文件和分析需求
    Agent->>Storage: 创建专用会话目录

    loop 多轮分析循环
        Agent->>LLM: 发送分析需求和上下文
        LLM->>Agent: 返回分析代码和推理
        Agent->>Executor: 执行Python代码
        Executor->>Storage: 保存图表文件
        Executor->>Agent: 返回执行结果

        alt 需要继续分析
            Agent->>LLM: 基于结果继续分析
        else 分析完成
            Agent->>LLM: 生成最终报告
            LLM->>Agent: 返回分析报告
            Agent->>Storage: 保存报告文件
        end
    end

    Agent->>User: 返回完整分析结果
```

## 🚀 快速开始

### 1. 环境准备

```bash
# 克隆项目（将 <你的用户名> 和 <仓库名> 替换为你自己的地址）
git clone https://github.com/<你的用户名>/<仓库名>.git
cd <仓库名>

# 建议使用虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装本项目（提供 data-analysis-agent 命令）
pip install -e .
```

### 2. 配置 API 密钥

项目通过 OpenAI 兼容协议调用 [DeepSeek](https://platform.deepseek.com/) 大模型。复制模板并填入你的 API Key：

```bash
cp .env.example .env
```

`.env` 文件内容：

```bash
# DeepSeek 官方 API 配置
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat

# 如果使用推理模型
# OPENAI_MODEL=deepseek-reasoner
```

> 说明：`deepseek-chat` 会使用温度参数；切换到推理模型（模型名包含 `reasoner` 或 `deepseek-r1`）时，程序会自动省略温度参数，以兼容推理接口。

### 3. 基本使用

```python
from data_analysis_agent import DataAnalysisAgent, LLMConfig

# 初始化智能体
llm_config = LLMConfig()
agent = DataAnalysisAgent(llm_config)

# 直接传入已上传数据集的 ID；文件兼容入口请使用下方的 quick_analysis(files=...)
report = agent.analyze(
    user_input="分析销售数据，生成趋势图表和关键指标",
    dataset_ids=[dataset_id],
)

# 输出结果
print(report["report_file_path"])       # Markdown 报告路径
print(report["word_report_file_path"])  # Word 报告路径
```

### 4. 便捷函数

```python
from data_analysis_agent import quick_analysis

report = quick_analysis(
    query="分析用户行为数据，重点关注转化率",
    files=["user_behavior.csv"],
    max_rounds=15,
)

# 同样支持控制 Word 报告输出
report = quick_analysis(
    query="分析销售数据，生成趋势图表",
    files=["sales.csv"],
    max_rounds=20,
    generate_word_report=True,
)
```

### 5. 数据集上传与 ID 分析

`DatasetUploadService` 是离线可验证的数据集上传边界：它校验 CSV 文件名、大小、编码、分隔符和结构，生成数据集 Profile，保存本地对象与元数据，并返回不透明的 `dataset_id`。分析流程只使用这个 ID 访问已上传的数据集。

本地存储和上传大小通过环境变量配置：

| 变量 | 说明 |
| --- | --- |
| `STORAGE_LOCAL_ROOT` | 本地数据集对象的存储根目录；未设置时使用输出目录下的默认数据集目录 |
| `MAX_UPLOAD_SIZE` | 单个上传文件允许的最大字节数 |

### LLM 网关与离线测试

模型调用统一通过 `data_analysis_agent.llm.LLMClient`。它支持 OpenAI 兼容接口（包括 DeepSeek），并负责超时、有限重试、稳定错误分类、结构化 JSON/Pydantic 校验、流式输出和调用指标。API Key 只从环境变量或配置对象读取，不要写入代码或提交到仓库。

```python
import os

from data_analysis_agent import LLMClient, LLMConfig

client = LLMClient(
    LLMConfig(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )
)
```

`LLMConfigurationError`、`LLMAuthenticationError`、`LLMTimeoutError`、`LLMRateLimitError` 和 `LLMStructuredOutputError` 等错误可用于区分配置、传输、供应商和输出协议问题。结构化输出必须先通过 JSON Schema 或 Pydantic 模型校验，未校验的模型文本不会进入代码执行阶段；旧 YAML 解析器仅保留给兼容测试替身使用。

没有 API Key 也可以运行测试。测试使用注入的 Fake Provider/FakeLLM，不会调用真实模型或对象存储 API：

```bash
pip install -e ".[dev]"
py -3 -m pytest -q
```

模块入口同样不需要 API Key 即可查看帮助：

```bash
python -m data_analysis_agent --help
```

### M08 编排器与离线验证

`DataAnalysisAgent` remains the compatibility facade for existing callers. 生产调用方可以使用 `AgentOrchestrator`，并通过 typed handlers、checkpoints 和 orchestration result 接入固定阶段流程。M08 不新增持久化要求。

模块启动和 agent 离线测试：

```powershell
python -m data_analysis_agent --help
pytest tests/agent -q
```

### M09 安全代码执行

生产环境的分析代码不会在 API 进程内执行。`APP_ENV=production` 会强制选择
`ContainerCodeExecutor`；Docker/runtime 不可用、镜像缺失或容器执行失败时任务会
失败，不会回退到本地 IPython。开发和测试环境默认使用旧的本地兼容执行器。

容器执行的安全边界包括：

- 输入数据只读挂载到固定的 `/input`，任务输出目录是唯一可写挂载并映射为 `/output`；
- 非 root 用户、只读根文件系统、`no-new-privileges`、丢弃 capabilities，以及 CPU、内存、PID、超时、输出字节数和文件数限制；
- 默认 `EXECUTION_NETWORK_MODE=none`，不会继承宿主环境变量，也不会把 API Key、数据库 URL 或宿主绝对路径放入代码协议；
- 每次执行使用独立容器和临时输入 staging；结果只保留代码哈希、耗时、退出状态、限制标志、错误码和输出文件元数据等最小审计信息。

配置示例：

```text
# development/test
EXECUTION_BACKEND=local
EXECUTION_IMAGE=
EXECUTION_NETWORK_MODE=none

# production
EXECUTION_BACKEND=container
EXECUTION_IMAGE=data-analysis-agent:production
EXECUTION_NETWORK_MODE=none
```

生产部署前必须准备可用的 Docker runtime 和分析镜像，并运行：

```bash
python -m alembic upgrade head
python -m data_analysis_agent --help
python -m pytest -q
```

### M10 分析结果与证据链

报告中的关键数字不应来自模型记忆或普通 stdout。结构化指标需要记录任务、数据集、
执行记录和代码哈希，并在进入报告提示词前完成复算：

```python
from uuid import uuid4

from data_analysis_agent import EvidenceRegistry
from data_analysis_agent.domain.models import MetricArtifact

task_id = uuid4()
registry = EvidenceRegistry(task_id=task_id)
metric = registry.register_metric(
    MetricArtifact(
        task_id=task_id,
        name="revenue",
        value=125.0,
        unit="CNY",
        formula="sum(revenue)",
        source_columns=("revenue",),
        source_dataset_ids=(uuid4(),),
        execution_id=uuid4(),
        code_hash="a" * 64,
    )
)
verified_metric = registry.verify_metric(metric.artifact_id, 125.0)
```

只有 `VERIFIED` 指标和已检查存在的当前任务图表会进入最终报告的结构化证据上下文。
模型不会从任意执行输出猜测指标；报告中的数字如果无法对应已验证指标，会在
`evidence_validation` 中标记为 `UNSUPPORTED_NUMERIC_CLAIM`，并以 `PENDING_CONFIRMATION`
（待确认）状态保留，而不是伪装成事实。`FACT`（事实）必须绑定已验证的指标或图表；
`INTERPRETATION`（解释）没有证据时也会保留原文，但会标记为待确认。

M10 的 `EvidenceRegistry` 目前是任务级内存服务，不新增数据库表。指标字段中的
`source_dataset_ids`、`execution_id`、`code_hash` 和 `source_columns` 用于复现与追溯；
图表路径会限制在当前任务输出目录内，越界、目录或缺失文件不会生成报告链接。

### M11 报告生成服务

`ReportService` 可以在不调用模型或网络的情况下，从已有分析叙述生成 Markdown、HTML
和 DOCX。当前公开导入路径和最小离线示例如下；输出目录必须预先存在：

```python
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.reports import ReportDocument, ReportFormat, ReportService

task_id = uuid4()
session_dir = Path("outputs/offline-report")
session_dir.mkdir(parents=True, exist_ok=True)

document = ReportDocument(
    task_id=task_id,
    output_root=str(session_dir),
    narrative_markdown="# 分析报告",
)
bundle = ReportService(allowed_output_root=session_dir).generate(
    document,
    formats={ReportFormat.MARKDOWN, ReportFormat.HTML},
)
```

`ReportService` 要求调用方显式提供受信任的任务输出根目录，报告目录必须位于该根目录内。

M11 只实现 Markdown、HTML 和 DOCX；PDF 是未来 renderer 扩展项，不属于本阶段交付。

### M12 后台任务 Worker

长时间分析通过 `TaskSubmissionService` 创建持久化任务，再将只含 `task_id` 的消息发送到
Celery/Redis。提交接口可以立即返回任务 ID；Worker 独立认领 `QUEUED` 任务并更新数据库状态、
阶段事件和错误信息。相同用户的相同幂等请求只入队一次，数据库认领也会阻止重复执行。

生产 Worker 依赖单独安装，当前测试不要求 Celery 或 Redis：

```bash
pip install -e ".[worker]"
data-analysis-agent-worker --env production --loglevel INFO
```

Worker 配置项为 `REDIS_URL`、`WORKER_MAX_RETRIES`、`WORKER_RETRY_BACKOFF_SECONDS` 和
`WORKER_STALE_AFTER_SECONDS`。Celery 使用 late acknowledgement、单条预取和 worker-lost
拒绝；网络错误等明确可重试错误使用指数退避，认证或协议错误不会无限重试。

取消任务会同时更新数据库、撤销 Celery 消息，并调用活动 Agent/执行器的停止回调。Worker
启动恢复时会把超过 `WORKER_STALE_AFTER_SECONDS` 的 `PENDING`、`QUEUED` 或 `RUNNING` 任务重新排队；
任务认领使用数据库条件更新，因此恢复产生的重复消息不会重复执行终态任务。数据库迁移
`20260927_0004` 为活动阶段重试增加了合法的 `QUEUED` 回退转换。
各格式独立生成：DOCX 或 HTML 渲染失败会在对应结果中返回错误，不会删除已经生成的
Markdown，也不会丢失 Agent 已有的分析结果。报告中的结构化指标和图表只接受当前任务
范围内、已经验证的证据；未验证指标、跨任务图表、越界路径和不存在的图片不会作为事实
证据进入输出。

### M13 后端 API

安装 API 依赖并启动本地服务：

```powershell
pip install -e ".[dev,api]"
Copy-Item .env.development.example .env
# 编辑 .env，至少设置：
# DATABASE_URL=sqlite:///./data_analysis_agent.sqlite3
# STORAGE_LOCAL_ROOT=./outputs/datasets
# Alembic does not load .env automatically; pass the same URL explicitly.
python -m alembic -x db_url=sqlite:///./data_analysis_agent.sqlite3 upgrade head
uvicorn data_analysis_agent.api.app:create_app --factory --reload
```

development 环境未配置 `REDIS_URL` 时不会注入内存 broker；API 仍可启动并提供非异步任务
能力，但提交、取消或重试任务会返回稳定的 `TASK_BROKER_NOT_CONFIGURED` 配置错误。只有
test 环境使用内存 broker 供离线契约测试。要运行完整的异步分析流程，请安装 Worker 依赖并配置 Redis：

```powershell
pip install -e ".[dev,api,worker]"
# 在 .env 中设置 REDIS_URL=redis://127.0.0.1:6379/0
docker run -d --rm -p 6379:6379 redis:7
python -m alembic -x db_url=sqlite:///./data_analysis_agent.sqlite3 upgrade head
data-analysis-agent-worker --env development --recover-stale --loglevel INFO
# 另一个终端启动 API
uvicorn data_analysis_agent.api.app:create_app --factory --reload
```

本地对象存储返回的 `download_url` 是后端签名令牌；前端应使用同一响应中的
`content_url` 访问受保护的 HTTP 内容端点。端点会再次校验当前用户、令牌有效期、
对象路径、大小和哈希，不会暴露宿主机文件路径。

服务提供 `/docs` 和 `/openapi.json`。数据集上传使用 `multipart/form-data`，分析任务使用
JSON；两者都会返回不透明的 ID：

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/datasets `
  -H "X-User-ID: 00000000-0000-0000-0000-000000000001" `
  -F "file=@sales.csv"

curl.exe -X POST http://127.0.0.1:8000/api/analysis-tasks `
  -H "Content-Type: application/json" `
  -H "X-User-ID: 00000000-0000-0000-0000-000000000001" `
  -d '{"query":"分析销售趋势","idempotency_key":"sales-2026-09"}'
```

`X-User-ID` 只用于开发和测试环境。生产环境必须注入 JWT/OIDC 等正式身份提供器，服务
不会回退到请求头身份。任务、事件、图表和报告读取都会按当前用户授权；错误响应统一
包含 `code`、`message`、`details` 和 `request_id`。

### 文件存储抽象

数据集、图表和报告通过统一的 `Storage` 接口保存。开发和测试环境默认使用
`LocalFileStorage`；生产环境使用 S3 兼容存储（包括 MinIO）。数据库只保存对象
URI、大小、哈希和内容类型等元数据，不保存 CSV、图片或报告本体。

```text
APP_ENV=test
STORAGE_LOCAL_ROOT=outputs/test/datasets
STORAGE_URL_EXPIRY=300
STORAGE_RETENTION_DAYS=30
```

生产环境还必须配置 `STORAGE_ENDPOINT` 和 `STORAGE_BUCKET`。前端或调用方应使用
结果中的 `report_download_url`、`html_report_download_url`、`word_report_download_url`
及对应的 `*_content_url` 或授权下载服务返回的临时 URL；不要把 `session_output_dir`、
`report_file_path` 或其他本地绝对路径作为
下载地址暴露给用户。旧的本地路径字段仍保留用于兼容已有脚本。

失败任务的 staging 目录只会在配置的输出根目录内清理，且只删除当前任务的对象；
存储对象缺失或元数据哈希/大小不一致时，会返回不包含本地路径的结构化一致性问题。

`files` 调用保留兼容性。适配器会在分析前校验并存储输入文件，然后把生成的 `dataset_id` 交给分析流程：

```python
report = quick_analysis(query="分析数据", files=["sales.csv"])
```

对于已上传的数据集，直接使用 `dataset_ids` 调用：

```python
report = quick_analysis(query="分析已上传数据", dataset_ids=[dataset_id])
```

发送给模型的内容只包含数据集 ID 和经过脱敏的 Profile 信息；不会把 `source_uri`、本地上传路径或用户原始文件名放入 prompt。数据集访问仍按所属用户校验。

### 6. 命令行使用

统一 CLI 要求显式传入输入文件；模块入口、安装后的命令和根目录兼容入口使用同一套实现：

```bash
python -m data_analysis_agent your_data.csv
data-analysis-agent your_data.csv
python main.py your_data.csv              # 兼容旧入口
python -m data_analysis_agent data1.csv data2.csv --query "分析销售数据"
```

以上命令不会自动补充 `cpc.csv` 或 `shop.csv`；请在命令中明确列出实际存在的文件。使用 `--help` 查看完整选项。

### 7. 自定义配置

```python
agent = DataAnalysisAgent(
    llm_config=llm_config,
    output_dir="custom_outputs",  # 自定义输出目录
    max_rounds=30,                # 最大分析轮数
    generate_word_report=True,    # 同时生成 Word 报告（默认）
)

# 如果只需要 Markdown 报告，可以关闭 Word 输出
markdown_only_agent = DataAnalysisAgent(
    llm_config=llm_config,
    generate_word_report=False,
)
```

### 可选 typed tool bridge

工具注册与执行是可选的，不会改变旧的 `analyze()` 调用、YAML/action
兼容契约。可以注入一个带 Pydantic 输入/输出模型的 fake handler：

```python
from pydantic import BaseModel

from data_analysis_agent import (
    DataAnalysisAgent,
    ToolDefinition,
    ToolRegistry,
    ToolRiskLevel,
)


class AddInput(BaseModel):
    value: int


class AddOutput(BaseModel):
    result: int


registry = ToolRegistry()
registry.register(
    ToolDefinition(
        name="double_value",
        description="Double one value.",
        input_model=AddInput,
        output_model=AddOutput,
        side_effect=False,
        network_access=False,
        max_runtime_seconds=1,
        required_permissions=frozenset(),
        risk_level=ToolRiskLevel.LOW,
    ),
    lambda value, context: {"result": value.value * 2},
)

agent = DataAnalysisAgent(llm=fake_llm, tool_registry=registry)
result = agent.execute_tool("double_value", {"value": 4})
```

`execute_tool()` 返回 typed `ToolCallResult`，并把 agent payload 追加到
`conversation_history`。不传 `tool_registry` 或 `tool_executor` 时，Agent
仍保持 legacy default；旧 YAML/action 路径继续按原契约运行。

## 📦 返回值说明

`analyze()` 返回一个包含分析全过程的字典：

| 字段 | 说明 |
| --- | --- |
| `session_output_dir` | 本次分析的会话输出目录 |
| `total_rounds` | 实际执行的分析轮数 |
| `analysis_results` | 每轮分析记录（代码、执行结果、LLM 响应） |
| `collected_figures` | 收集到的图表信息列表 |
| `conversation_history` | 完整对话历史 |
| `final_report` | 最终报告 Markdown 内容 |
| `report_file_path` | Markdown 报告文件路径 |
| `word_report_file_path` | Word 报告文件路径（未生成时为 `None`） |
| `word_report_generated` | Word 报告是否生成成功 |
| `word_report_error` | Word 报告生成失败时的错误信息 |
| `execution_audits` | 每次 typed 代码执行的最小安全审计元数据，不包含源代码或宿主路径 |
| `metric_artifacts` | 带数据集、执行 ID、代码哈希和验证状态的结构化指标 |
| `chart_artifacts` | 当前任务图表的来源、路径元数据和文件验证状态 |
| `evidence_claims` | 事实/解释及其证据支持状态 |
| `evidence_validation` | 报告数字、缺失图表和待确认 claim 的校验结果 |

Word 转换处理报告 Markdown 中的标题、段落、列表、行内粗体/斜体、代码块和图片语法；识别 `【部分总结】` 与 `【分析要点】` 语义标记；图片会以当前会话输出目录为边界进行路径校验，并以带边框和阴影的板块嵌入。

## 🎨 流程可视化

```mermaid
stateDiagram-v2
    [*] --> 数据加载
    数据加载 --> 数据探索: 成功加载
    数据加载 --> 编码修复: 编码错误
    编码修复 --> 数据探索: 修复完成

    数据探索 --> 数据清洗: 探索完成
    数据清洗 --> 统计分析: 清洗完成
    统计分析 --> 可视化生成: 分析完成

    可视化生成 --> 图表保存: 图表生成
    图表保存 --> 结果评估: 保存完成

    结果评估 --> 继续分析: 需要更多分析
    结果评估 --> 报告生成: 分析充分
    继续分析 --> 统计分析

    报告生成 --> [*]: 完成
```

## 🔧 配置说明

### LLMConfig

```python
@dataclass
class LLMConfig:
    provider: str = "deepseek"
    api_key: str = os.environ.get("OPENAI_API_KEY", "")
    base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
    model: str = os.environ.get("OPENAI_MODEL", "deepseek-chat")
    max_tokens: int = 8192
    temperature: float = 0.1
```

支持通过环境变量或直接传参两种方式配置，也可以通过 `LLMConfig.from_dict()` 从字典创建。

### 开发/测试代码执行器兼容限制

开发和测试环境仍保留基于 IPython 的兼容执行器。它通过 AST 静态分析限制可导入
的库和危险函数调用，但不是生产安全边界；生产必须使用上面的容器后端：

```python
ALLOWED_IMPORTS = {
    'pandas', 'pd', 'numpy', 'np',
    'matplotlib', 'matplotlib.pyplot', 'plt',
    'duckdb', 'scipy', 'sklearn',
    'plotly', 'dash', 'requests', 'urllib',
    'os', 'sys', 'json', 'csv', 'datetime', 'time',
    'math', 'statistics', 're', 'pathlib', 'io',
    'collections', 'itertools', 'functools', 'operator',
    'warnings', 'logging', 'copy', 'pickle', 'gzip', 'zipfile',
    'typing', 'dataclasses', 'enum', 'sqlite3'
}
```

- 🔒 仅允许预定义的数据分析库，禁止其他导入
- 🚫 禁止调用 `exec` / `eval` / `open` / `__import__` 等危险函数
- 🚫 禁止 `plt.show()`，图片统一保存到会话目录
- 🐼 预导入 pandas、numpy、matplotlib、duckdb，变量跨代码块保持

## 🎯 最佳实践

### 1. 数据准备

- ✅ 使用 CSV 格式，支持 UTF-8 / GBK 编码
- ✅ 确保列名清晰、无特殊字符
- ✅ 数据量适中（建议 < 100MB）

### 2. 查询编写

- ✅ 使用清晰的中文描述分析需求
- ✅ 指定想要的图表类型和关键指标
- ✅ 明确分析的目标和重点

### 3. 结果解读

- ✅ 检查生成的图表是否符合预期
- ✅ 阅读分析报告中的关键发现
- ✅ 根据需要调整查询重新分析

## 🚨 注意事项

### 安全限制

- 🔒 仅支持预定义的数据分析库
- 🔒 不允许文件系统操作（除图片保存）
- 🔒 不支持网络请求（除 LLM 调用）

### 性能考虑

- ⚡ 大数据集可能导致分析时间较长
- ⚡ 复杂分析任务可能需要多轮交互
- ⚡ API 调用频率受到模型限制

### 兼容性

- 🐍 Python 3.10+
- 📊 支持 pandas 兼容的数据格式
- 🖼️ 需要 matplotlib 中文字体支持（SimHei）

## 🐛 故障排除

**Q: 图表中文显示为方框？**
A: 确保系统安装了 SimHei 字体，或在 `code_executor.py` 的 `_setup_chinese_font()` 中指定其他中文字体。

**Q: API 调用失败？**
A: 检查 `.env` 文件中的 API 密钥和端点配置，确保网络连接正常。也可以临时将 `OPENAI_MODEL` 切换为 `deepseek-chat` 排除推理模型接口问题。

**Q: 数据加载错误？**
A: 检查文件路径和编码格式，支持 UTF-8、GBK 等常见编码。

**Q: 分析结果不准确？**
A: 尝试提供更详细的分析需求，或检查原始数据质量。

**Q: Mermaid 流程图无法正常显示？**
A: GitHub、Typora、VS Code 等支持 Mermaid 的环境可直接渲染；若本地无法显示，请使用支持 Mermaid 的 Markdown 编辑器。

分析过程中的错误信息会保存在会话目录中，便于调试和优化。

## 💾 持久化与部署

生产环境使用 MySQL。配置示例：

```text
APP_ENV=production
DATABASE_URL=mysql+pymysql://user:password@host:3306/data_analysis

# 创建或升级生产数据库结构
python -m alembic upgrade head

# 离线测试使用临时 SQLite，不调用 MySQL、LLM 或对象存储
python -m pytest tests/database tests/integration -q
```

`datasets.source_uri`、`artifacts.file_path` 和 `reports.storage_uri` 都是外部地址；持久化层只保存元数据，不读取、复制或写入 CSV、图片、Markdown 或 DOCX 内容。相同用户和 `idempotency_key` 的重复请求会返回原任务；同一用户复用 key 但请求内容变化时会报冲突。

## 🤝 贡献指南

欢迎贡献代码和改进建议！

1. Fork 项目
2. 创建功能分支（`git checkout -b feature/xxx`）
3. 提交更改（`git commit -m 'feat: xxx'`）
4. 推送到分支（`git push origin feature/xxx`）
5. 创建 Pull Request

## 📄 许可证

本项目基于 [MIT](LICENSE) 许可证开源。

## 🔄 更新日志

### v1.0.0

- ✨ 初始版本发布
- 🎯 支持自然语言数据分析
- 📊 集成 matplotlib 图表生成
- 📝 自动报告生成功能（Markdown + Word）
- 🔒 安全的代码执行环境

---

<div align="center">

**🚀 让数据分析变得更智能、更简单！**

如果这个项目对你有帮助，欢迎 ⭐ Star 支持！

</div>
