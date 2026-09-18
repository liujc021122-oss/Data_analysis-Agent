# M04 文件上传与数据校验设计

## 1. 目标与范围

M04 将用户上传的 CSV 转换为经过安全校验、可追踪、可供后续分析使用的数据集。上传逻辑独立于 Agent，Agent 只接收 `dataset_id`，不接收用户文件路径。

本阶段支持 CSV，预留 Excel 扩展点但不实现 Excel 解析。数据库继续只保存文件元数据和有限的结构化 Profile 摘要，不保存 CSV 或图片本体。

## 2. 已确认的设计决策

- 存储采用 `StorageBackend` 抽象。开发和测试使用受控本地 staging，生产通过对象存储适配器接入。
- 核心上传接口接收二进制 `BinaryIO`/file-like 对象和原始文件名。CLI 路径及 Web `UploadFile` 由外层适配，不让核心服务依赖 Web 框架。
- 文件使用随机 UUID 存储键。原始文件名仅作为元数据，绝不参与目标路径拼接。
- CSV 采用 BOM 优先、`charset-normalizer` 检测和置信度校验。支持逗号、分号、制表符和竖线。
- 结构异常严格拒绝：空文件、编码不可识别、字段数不一致、空列名、重复列名、非法控制字符和不支持的扩展名都不能进入分析流程。
- 敏感字段使用列名规则和值模式抽样识别，只输出字段名、风险类型和检测来源，不输出原始敏感值。
- Profile 使用结构化模型，序列化后保存到现有 `datasets.metadata_json`，本阶段不增加独立 Profile 表。
- 新增 `dataset_ids` Agent 入口；保留 `files` 作为兼容入口，但必须先上传、校验并转换成 dataset ID。

## 3. 分层架构

```text
file-like
    |
    v
DatasetUploadService
    |-- size and extension validation
    |-- StorageBackend: controlled staging / object storage
    |-- CsvInspector: encoding, delimiter, structure, profile
    |-- Dataset repository: metadata and profile persistence
    v
DatasetUploadResult(dataset_id, metadata, DatasetProfile)
    |
    v
Agent / DatasetResolver
    |-- receives dataset_id only
    |-- checks owner/task access
    |-- opens data through StorageBackend
```

### 3.1 存储边界

`StorageBackend` 提供以下能力：

```python
class StorageBackend(Protocol):
    def put_stream(self, stream, *, key: str, max_bytes: int) -> StoredObject: ...
    def open(self, uri: str) -> BinaryIO: ...
    def delete(self, uri: str) -> None: ...
```

`LocalStorageBackend` 的根目录由 `STORAGE_LOCAL_ROOT` 控制，所有解析后的路径必须通过规范化路径检查，最终仍位于该根目录内。生产对象存储实现使用 `STORAGE_ENDPOINT` 和 `STORAGE_BUCKET`，不改变上层接口。

上传过程不信任客户端 `Content-Length`。流式写入时计算 SHA-256 和实际字节数，超过 `MAX_UPLOAD_SIZE` 立即终止并清理暂存文件。存储键由 UUID 生成，避免路径穿越和文件名冲突。

### 3.2 上传服务

```python
class DatasetUploadService:
    def upload(
        self,
        stream: BinaryIO,
        *,
        original_filename: str,
        owner_id: UUID,
    ) -> DatasetUploadResult: ...
```

处理顺序：

1. 校验原始文件名存在且扩展名为 `.csv`；文件名仅用于元数据。
2. 将流安全写入受控 staging，同时限制大小并计算哈希。
3. 通过 `CsvInspector` 严格解析并生成 `DatasetProfile`。
4. 校验成功后提交为不可变存储对象。
5. 在 Unit of Work 中创建 `DatasetRecord`，`source_uri` 保存存储 URI，`metadata_json` 保存 Profile 摘要。
6. 返回 `dataset_id`、原始文件名、大小、哈希、内容类型和 Profile。

任何校验失败都不创建 Dataset。数据库提交失败时回滚并删除对象；对象删除失败只记录不泄露路径的清理日志。存储失败不创建数据库记录。

## 4. 领域模型与错误契约

### 4.1 Profile 模型

新增 `datasets.models` 中的结构化模型：

- `DatasetProfile`：`encoding`、`delimiter`、`row_count`、`column_count`、`columns`、最多 20 行 `preview_rows`、`sensitive_fields`。
- `ColumnProfile`：列名、推断类型、非空数量、缺失数量、缺失率。
- `SensitiveField`：字段名、风险类型、检测来源（`name` 或 `value`）。
- `DatasetUploadResult`：数据集 ID、原始文件名、大小、哈希、内容类型和 Profile。

Profile 的 JSON 序列化必须成功，且预览值不能包含被识别的敏感原值。Profile 只保存有限摘要，不能保存完整 CSV 内容。

### 4.2 校验规则

- 只接受 `.csv`，大小不能超过 `MAX_UPLOAD_SIZE`。
- 文件必须非空，且必须能被可靠解码为文本。
- 编码优先识别 UTF-8 BOM；无 BOM 时使用 `charset-normalizer`，低置信度或无法解码时拒绝。
- 分隔符仅允许逗号、分号、制表符和竖线；必须能够得到稳定的表头和列数。
- 表头必须存在，列名去除首尾空白后不得为空且不得重复。
- 每一行的字段数必须与表头一致。
- 拒绝 NUL 字符和非法控制字符。
- 生成最多前 20 行预览、行数、列数、推断类型、缺失计数和缺失率。
- 敏感识别先检查列名，再对有限样本检查邮箱、手机号、身份证号等值模式；只返回风险标记，不记录原始值。

### 4.3 错误类型

上传模块定义稳定的错误类型和代码：

- `UploadValidationError`
- `StorageError`
- `DatasetPersistenceError`
- `DatasetAccessDeniedError`

至少包含以下校验错误码：`FILE_TOO_LARGE`、`UNSUPPORTED_EXTENSION`、`EMPTY_FILE`、`ENCODING_DETECTION_FAILED`、`INVALID_CSV`、`DUPLICATE_COLUMNS`、`EMPTY_COLUMN_NAME`、`CONTROL_CHARACTER`。API 层将这些错误转换为结构化错误响应，并标明上传校验、存储、持久化或访问模块。

## 5. Agent 与 API 接入

新增 `DatasetResolver`：

```python
class DatasetResolver:
    def open_for_user(self, dataset_id: UUID, *, owner_id: UUID) -> BinaryIO: ...
```

Resolver 必须校验数据集存在且属于当前用户。Agent 的运行环境只暴露按 ID 加载数据的后端能力，例如 `load_dataset(dataset_id)`；模型上下文只包含 `dataset_id`、Schema、统计信息和脱敏预览，不包含 `source_uri`、本地绝对路径或用户原始路径。

`quick_analysis` 新增 `dataset_ids` 参数。旧的 `files` 参数保留为兼容入口，先经过路径适配、上传服务和完整校验，再转换为 dataset ID。旧入口不得把原始路径拼入模型提示词。

## 6. 测试设计

### 6.1 存储单元测试

- 存储键随机且不使用原始路径。
- Local backend 的规范化路径永远不能越过根目录。
- 读取和删除只作用于根目录内对象。
- 流式写入超过上限会中止并清理。

### 6.2 CSV 检查单元测试

- UTF-8、UTF-8 BOM 和 GBK 编码。
- 逗号、分号、制表符和竖线分隔符。
- 空文件、非法编码、非法扩展名、重复/空列名。
- 行字段数不一致、NUL 和非法控制字符。
- 前 20 行预览、列类型、行数、缺失统计。
- 敏感字段只返回风险信息，预览不包含敏感原值。

### 6.3 上传集成测试

- 合法上传生成 dataset ID 并持久化 URI、大小、哈希和 Profile。
- 所有非法输入不会创建 Dataset。
- 数据库提交失败会回滚并清理存储对象。
- 用户不能访问其他用户的数据集。
- 解析、存储和持久化失败分别能定位到对应模块。

### 6.4 Agent 回归测试

- 提示词包含 dataset ID 和 Profile，但不包含原始文件路径或存储 URI。
- `quick_analysis(files=...)` 兼容且经过校验。
- `quick_analysis(dataset_ids=...)` 使用持久化数据集。
- 测试使用 fake LLM 和本地 fake storage，不调用真实模型 API 或对象存储。

## 7. 非目标与后续扩展

- 本阶段不实现 Excel 解析。
- 本阶段不保存文件本体到数据库。
- 本阶段不实现异步队列或大文件分块上传。
- 本阶段不自动脱敏原始文件；只保证 Profile 预览和模型上下文不暴露识别出的敏感值。
- 对象存储生产适配器只需遵守 `StorageBackend` 契约，具体云厂商实现可在后续模块完成。
