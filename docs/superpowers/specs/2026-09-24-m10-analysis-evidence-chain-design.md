# M10 分析结果与证据链设计

## 目标

为分析结果建立可追溯的证据链，使报告中的关键数字和事实结论只能来自
结构化指标或已验证的图表，而不是模型记忆、自由发挥或不可复现的文本输出。

M10 只负责领域模型、内存中的证据登记/校验和 Agent 接入；不引入新的数据库表，
也不重写 M08 状态机、M09 执行沙箱或 M11 报告渲染服务。

## 当前上下文

项目已经存在以下基础：

- `domain.models` 已有 `MetricArtifact`、`ChartArtifact` 和 `AgentState` 字段；
- M09 的 `ExecutionAudit` 已记录 task、代码 SHA-256、执行时间、状态和输出文件；
- M05 已提供任务级图表/报告存储与文件边界检查；
- `DataAnalysisAgent` 仍保留 `analysis_results`、`collected_figures` 和兼容结果字典；
- M07 已提供工具注册中心和 `validate_metric` 工具；
- M03 数据库可以持久化通用 Artifact 元数据，但尚未有证据链专用表。

因此 M10 采用“扩展已有领域模型 + 独立证据服务 + 兼容接入”的方案。

## 设计方案

### 1. 领域模型

扩展已有 `MetricArtifact`，保留当前字段和默认值以兼容已有调用，同时增加：

- `task_id`：所属分析任务；
- `formula`：可读的计算公式或定义；
- `source_columns`：参与计算的列名；
- `source_dataset_ids`：参与计算的数据集 ID；
- `execution_id`：产生该指标的执行记录 ID；
- `code_hash`：产生该指标的代码 SHA-256；
- `computed_at`：指标计算时间；
- `verification_status`：`UNVERIFIED`、`VERIFIED` 或 `PENDING_CONFIRMATION`；
- `recomputed_value`：系统复算值（可选）；
- `tolerance`：复算允许误差。

扩展已有 `ChartArtifact`，增加：

- `task_id`；
- `chart_type`；
- `source_metric_ids`；
- `execution_id`；
- `code_hash`；
- `verification_status`；
- `checked_at`。

新增两个轻量模型：

- `EvidenceClaim`：一条报告事实或解释，包含 `kind=FACT|INTERPRETATION`、文本、
  指标 ID、图表 ID 和校验状态；
- `EvidenceValidation`：报告校验结果，包含通过/待确认的 claim、未绑定数字、
  缺失图表和稳定的错误码。

所有新增字段必须严格类型校验、拒绝未知字段、要求带时区时间，并能通过
`model_dump_json()` 序列化。数值必须有限，UUID 和哈希必须使用现有领域校验规则。

### 2. `EvidenceRegistry`

新增 `services/evidence.py`，提供任务级、内存中的证据登记服务：

```python
registry.register_metric(metric)
registry.register_chart(chart)
registry.verify_metric(metric_id, recomputed_value)
registry.check_chart(chart_id)
registry.validate_claim(claim)
registry.validate_report(markdown, task_id)
registry.snapshot(task_id)
```

规则如下：

- 注册对象的 `task_id` 必须与注册表当前任务一致；
- 相同任务、名称、单位、来源执行记录和公式的指标重复登记时返回同一对象，
  不产生第二个事实；不同值则返回稳定冲突错误；
- `verify_metric` 使用绝对/相对容差复算，结果一致才转为 `VERIFIED`；
- 指标缺少数据集、执行记录或代码哈希时不能标记为 `VERIFIED`；
- 图表检查使用解析后的任务输出根目录，拒绝越界路径、目录和不存在文件；
- 缺失图表不进入报告链接，状态为 `PENDING_CONFIRMATION`；
- `FACT` 必须至少绑定一个已验证指标或图表；
- `INTERPRETATION` 可以没有直接数字，但没有证据时必须标记为
  `PENDING_CONFIRMATION`，不能伪装成事实；
- 所有跨任务引用、未知 ID 和冲突登记都返回可断言的领域错误，不暴露宿主路径。

### 3. 报告数字校验

M10 不负责 Markdown/HTML/DOCX 渲染，但提供报告前置校验：

1. Agent 将已验证指标生成结构化 evidence context，作为最终报告提示词的唯一数字来源；
2. `validate_report` 忽略代码块、URL、图片路径和 UUID 等非事实数字；
3. 报告中的关键数字必须能在当前任务已验证指标中找到，允许配置的绝对/相对误差；
4. 无法匹配的数字生成 `UNSUPPORTED_NUMERIC_CLAIM`，报告仍可返回，但对应 claim
   标记为 `PENDING_CONFIRMATION`；
5. 校验输出随 Agent 结果返回，M11 报告服务将据此决定是否显示“待确认”提示。

M10 不把模型生成的解释直接当作事实。模型解释只有在明确绑定指标/图表后才能
标记为 `SUPPORTED`，否则保留原文但携带待确认状态。

### 4. Agent 接入

`DataAnalysisAgent` 增加可选的 `evidence_registry` 注入；未传入时为每个分析任务
创建新的注册表。现有执行路径保持不变：

- 每次 M09 执行审计生成稳定的 `execution_id`；
- Agent 提供 `register_metric(...)` 兼容方法，未来工具或结构化分析阶段通过该入口
  登记指标；不从普通 stdout 或模型自由文本猜测指标；
- `collect_figures` 成功后自动登记 `ChartArtifact`，并复用当前执行审计的
  `execution_id`/`code_hash`；
- 图表路径先通过现有 executor/output scope 校验，再进入证据注册表；
- 最终报告提示词只注入已验证指标和有效图表摘要；
- Agent 结果增加 `metric_artifacts`、`chart_artifacts`、`evidence_claims` 和
  `evidence_validation`，保留所有已有兼容字段；
- 任务失败、取消或清理时，证据注册表只存在于当前 Agent 实例，不产生跨任务泄露。

M09 生产容器路径不能把宿主回调函数注入代码。M10 的第一版指标登记通过 Agent/工具
边界完成；容器内结构化指标写回协议留给后续工具增强，不以解析不可信 stdout 作为替代。

### 5. 持久化边界

M10 的 registry 是内存快照，`AgentState` 保存同一任务的结构化指标和图表引用，
结果可以 JSON 序列化。M03 数据库只继续保存通用 Artifact 文件元数据；指标专用表、
证据 claim 表和跨重启恢复属于后续持久化任务，不在本模块偷偷扩展迁移。

## 文件边界

计划新增或修改：

- `src/data_analysis_agent/domain/enums.py`：证据类别和校验状态；
- `src/data_analysis_agent/domain/models.py`：扩展指标/图表模型，新增 claim/validation；
- `src/data_analysis_agent/services/evidence.py`：登记、去重、复算、图表和报告校验；
- `src/data_analysis_agent/services/__init__.py`、顶层导出：稳定公共入口；
- `src/data_analysis_agent/execution/models.py`、`agent_session.py`：补充 execution ID；
- `src/data_analysis_agent/agent/core.py`、`legacy_adapter.py`：Agent 注册和结果接入；
- `tests/domain/test_evidence_models.py`：模型严格校验和 JSON 契约；
- `tests/services/test_evidence_registry.py`：登记、冲突、复算和路径边界；
- `tests/agent/test_evidence_integration.py`：Agent 图表/指标/报告校验接入；
- `tests/integration/test_evidence_flow.py`：离线端到端证据链；
- `README.md`：指标来源、事实/解释和待确认行为说明。

不修改 M11 的报告渲染链，不引入真实模型 API、Docker 或新的生产外部服务依赖。

## 错误处理

证据错误使用稳定错误码，至少包括：

- `EVIDENCE_TASK_MISMATCH`；
- `METRIC_CONFLICT`；
- `METRIC_NOT_REPRODUCIBLE`；
- `CHART_PATH_INVALID`；
- `CHART_NOT_FOUND`；
- `UNSUPPORTED_NUMERIC_CLAIM`；
- `EVIDENCE_REFERENCE_NOT_FOUND`。

错误信息不包含原始代码、密钥、宿主绝对路径或模型原始 payload。报告校验失败默认
不丢弃分析结果，而是返回待确认元数据；只有模型或执行流程本身失败才沿用现有
Agent/编排错误契约。

## 测试策略

- 领域模型：非法字段、非法状态、非有限数字、无时区时间、越界引用和 JSON 序列化；
- 登记服务：任务隔离、幂等登记、冲突检测、复算成功/失败；
- 图表边界：存在文件、缺失文件、目录、越界路径和存储模式；
- 报告校验：事实数字匹配、未知数字待确认、解释与事实区分、代码块/URL 忽略；
- Agent：自动登记图表、指标通过 Agent 入口登记、最终结果携带证据快照；
- 全链路：FakeLLM、fake executor/storage，无真实 API Key、网络或 Docker；
- 保持 M00-M09 全量回归通过。

## 验收标准映射

| 验收标准 | 实现/测试证明 |
| --- | --- |
| 关键指标可追溯 | MetricArtifact 强制 task/数据集/执行 ID/代码哈希/来源列契约 |
| 报告数字来自结构化指标 | report validator + Agent evidence context |
| 图表不存在不生成错误链接 | registry path check + chart integration test |
| 无证据结论标记待确认 | EvidenceClaim/Validation 状态机 |
| 重复计算结果一致 | metric idempotency + recomputation tests |

