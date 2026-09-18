# M04 文件上传与数据校验实施计划

## M04 Final Review Completion Ledger

- Task 1: complete.
- Task 2: complete.
- Task 3: complete, including strict unsupported-delimiter rejection and bounded sensitivity sampling.
- Task 4: complete.
- Task 5: complete, including Profile-driven dataset loading and shared `files` compatibility adaptation.
- Task 6: complete, including README, offline acceptance coverage, and final verification.
- Final review fixes: complete after the RED/GREEN regression cycle recorded in `final-review-fix-report.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CSV 上传转换为经过严格校验、可持久化、只能通过 `dataset_id` 访问的数据集，并让 Agent/`quick_analysis` 不再把用户文件路径放入模型上下文。

**Architecture:** 新增 `data_analysis_agent.datasets` 分层模块。`CsvInspector` 只负责字节内容识别和 Profile 生成，`StorageBackend` 只负责安全存储，`DatasetUploadService` 负责编排校验、存储和 Dataset 元数据持久化，`DatasetResolver` 负责按用户归属打开数据。Agent 通过注入的 resolver 暴露 `load_dataset(dataset_id)`，兼容入口 `files` 先转换为不透明的 dataset ID。

**Tech Stack:** Python 3.10+, Pydantic 2, pandas, `charset-normalizer`, SQLAlchemy 2/Alembic, pytest, 本地 fake storage；测试不调用真实模型 API、对象存储或 MySQL。

## Global Constraints

- 只接受 `.csv`；Excel 只保留扩展点，不在 M04 实现。
- 文件大小由 `MAX_UPLOAD_SIZE` 限制，不能信任客户端 `Content-Length`。
- 文件存储使用 `StorageBackend`；开发/测试为受控本地目录，生产通过对象存储适配器。
- 原始文件名只保存为元数据；存储键由 UUID 生成，用户路径不得参与路径拼接。
- CSV 编码按 BOM 优先、`charset-normalizer` 检测和置信度校验处理；分隔符仅允许逗号、分号、制表符、竖线。
- 空文件、非法编码、字段数不一致、空列名、重复列名、NUL/非法控制字符必须拒绝。
- 预览最多 20 行；敏感字段只返回字段名、风险类型和检测来源，不保存或输出敏感原值。
- 数据库只保存 URI、文件名、大小、哈希和 Profile JSON 摘要，不保存文件本体。
- Agent 提示词只能包含 `dataset_id`、Schema、统计值和脱敏预览，不包含 `source_uri`、绝对路径或用户原始路径。
- 现有 `quick_analysis(files=...)` 保留兼容性，但必须先校验并转换为 dataset ID；新增 `dataset_ids` 入口。
- 每个任务遵循 RED → 确认失败 → GREEN → 确认通过 → 提交的顺序。

---

## 文件地图

| 文件 | 责任 |
| --- | --- |
| `src/data_analysis_agent/datasets/models.py` | Profile、上传结果和存储对象的 Pydantic/值对象模型 |
| `src/data_analysis_agent/datasets/errors.py` | 稳定错误码和上传/存储/访问异常 |
| `src/data_analysis_agent/datasets/storage.py` | `StorageBackend` 协议和安全本地实现 |
| `src/data_analysis_agent/datasets/inspection.py` | 编码、分隔符、CSV 结构、Schema、预览和敏感字段检查 |
| `src/data_analysis_agent/datasets/service.py` | 元数据存储适配器、上传编排和失败清理 |
| `src/data_analysis_agent/datasets/resolver.py` | 按 dataset ID 和用户归属打开文件、读取 Profile |
| `src/data_analysis_agent/datasets/__init__.py` | 数据集模块的唯一导出入口 |
| `src/data_analysis_agent/config/settings.py` | `STORAGE_LOCAL_ROOT` 类型化配置和生产配置校验 |
| `src/data_analysis_agent/api/schemas.py` | 上传响应 DTO |
| `src/data_analysis_agent/agent/core.py` | dataset ID 上下文、`load_dataset` 注入和兼容入口 |
| `src/data_analysis_agent/agent/prompts.py` | 只使用 dataset ID 的模型指令 |
| `src/data_analysis_agent/cli.py` | 可选 `--dataset-id` 入口及参数互斥校验 |
| `pyproject.toml`, `requirements.txt` | 显式声明 `charset-normalizer` |
| `tests/datasets/*` | 数据集模块单元/集成测试 |
| `tests/integration/test_analysis_flow.py` | 不泄露路径的 Agent 离线流程测试 |
| `tests/public_api/test_public_api_contract.py` | 新公共参数签名测试 |
| `README.md` | 上传、dataset ID 和兼容入口说明 |

---

### Task 1: 数据集配置、模型和错误契约

**Files:**
- Create: `src/data_analysis_agent/datasets/__init__.py`
- Create: `src/data_analysis_agent/datasets/models.py`
- Create: `src/data_analysis_agent/datasets/errors.py`
- Modify: `src/data_analysis_agent/config/settings.py`
- Modify: `src/data_analysis_agent/api/schemas.py`
- Modify: `src/data_analysis_agent/__init__.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Test: `tests/datasets/test_models.py`
- Modify: `tests/config/test_settings_contract.py`
- Modify: `tests/api/test_schemas.py`

**Interfaces:**
- Produces `DatasetProfile`, `ColumnProfile`, `SensitiveField`, `DatasetUploadResult`, `StoredObject` and `DatasetErrorCode`.
- Produces `Settings.storage_local_root: Path`.
- Produces `DatasetUploadResponse` for API-layer serialization.

- [ ] **Step 1: Write the failing model and configuration tests**

```python
# tests/datasets/test_models.py
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_analysis_agent.datasets.errors import DatasetErrorCode, UploadValidationError
from data_analysis_agent.datasets.models import (
    ColumnProfile,
    DatasetProfile,
    DatasetUploadResult,
    SensitiveField,
)


def test_profile_serializes_to_json_with_at_most_twenty_preview_rows():
    profile = DatasetProfile(
        encoding="utf-8",
        delimiter=",",
        row_count=1,
        column_count=1,
        columns=(
            ColumnProfile(
                name="email",
                inferred_type="string",
                non_null_count=1,
                missing_count=0,
                missing_rate=0.0,
            ),
        ),
        preview_rows=({"email": "[REDACTED]"},),
        sensitive_fields=(
            SensitiveField(
                column_name="email",
                risk_type="email",
                detected_by=("name", "value"),
            ),
        ),
    )

    payload = profile.model_dump_json()

    assert '"encoding":"utf-8"' in payload
    assert "[REDACTED]" in payload
    assert "email@example.com" not in payload


def test_profile_rejects_negative_counts_and_unknown_fields():
    with pytest.raises(ValidationError, match="missing_count"):
        ColumnProfile(
            name="value",
            inferred_type="number",
            non_null_count=1,
            missing_count=-1,
            missing_rate=0.0,
        )

    with pytest.raises(ValidationError, match="extra"):
        DatasetProfile(
            encoding="utf-8",
            delimiter=",",
            row_count=0,
            column_count=1,
            columns=(),
            preview_rows=(),
            sensitive_fields=(),
            unexpected=True,
        )


def test_validation_error_exposes_stable_code_without_echoing_path():
    error = UploadValidationError(
        DatasetErrorCode.UNSUPPORTED_EXTENSION,
        "only CSV files are supported",
        details={"filename": "report.csv"},
    )

    assert error.code is DatasetErrorCode.UNSUPPORTED_EXTENSION
    assert "report.csv" in error.details["filename"]
    assert "C:\\secret" not in str(error)
```

```python
# tests/config/test_settings_contract.py
from pathlib import Path

from data_analysis_agent.config.settings import load_settings


def test_settings_derives_controlled_local_storage_root(tmp_path):
    settings = load_settings(
        app_env="test",
        environ={"DATABASE_URL": "sqlite:///:memory:"},
        dotenv_dir=tmp_path,
    )

    assert settings.storage_local_root == Path("outputs/test/datasets")


def test_settings_accepts_explicit_storage_local_root(tmp_path):
    settings = load_settings(
        app_env="development",
        environ={
            "DATABASE_URL": "sqlite:///:memory:",
            "STORAGE_LOCAL_ROOT": str(tmp_path / "uploads"),
        },
        dotenv_dir=tmp_path,
    )

    assert settings.storage_local_root == tmp_path / "uploads"
```

Run: `pytest tests/datasets/test_models.py tests/config/test_settings_contract.py -q`

Expected: FAIL because the datasets module, new Settings field, and error code do not yet exist.

- [ ] **Step 2: Add the dependency and implement the value models**

Add exactly `charset-normalizer>=3.0,<4.0` to both `pyproject.toml` project dependencies and `requirements.txt`.

Implement the following model shape in `models.py`:

```python
class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: StrictStr
    inferred_type: Literal["empty", "boolean", "integer", "number", "datetime", "string", "mixed"]
    non_null_count: StrictInt = Field(ge=0)
    missing_count: StrictInt = Field(ge=0)
    missing_rate: StrictFloat = Field(ge=0.0, le=1.0)


class SensitiveField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    column_name: StrictStr
    risk_type: StrictStr
    detected_by: tuple[Literal["name", "value"], ...]


class DatasetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    encoding: StrictStr
    delimiter: StrictStr
    row_count: StrictInt = Field(ge=0)
    column_count: StrictInt = Field(gt=0)
    columns: tuple[ColumnProfile, ...]
    preview_rows: tuple[dict[str, Any], ...] = ()
    sensitive_fields: tuple[SensitiveField, ...] = ()


class DatasetUploadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: UUID
    original_filename: StrictStr
    content_type: StrictStr = "text/csv"
    size_bytes: StrictInt = Field(ge=0)
    checksum: StrictStr
    profile: DatasetProfile
```

`StoredObject` 是不可变 dataclass，字段为 `uri: str`, `size_bytes: int`, `checksum: str`, `content_type: str = "text/csv"`。错误枚举使用 `class DatasetErrorCode(str, Enum)`，至少包含 `FILE_TOO_LARGE`, `UNSUPPORTED_EXTENSION`, `EMPTY_FILE`, `ENCODING_DETECTION_FAILED`, `INVALID_CSV`, `DUPLICATE_COLUMNS`, `EMPTY_COLUMN_NAME`, `CONTROL_CHARACTER`, `STORAGE_FAILURE`, `DATASET_PERSISTENCE_FAILURE`, `DATASET_ACCESS_DENIED`。

`Settings` 增加 `storage_local_root: Path`。`load_settings()` 使用 `STORAGE_LOCAL_ROOT`，缺省值为 `selected_output / "datasets"`；生产环境在缺失 `STORAGE_ENDPOINT` 或 `STORAGE_BUCKET` 时，将它们加入已有的明确缺失配置错误。

`DatasetUploadResponse` 使用 `DatasetUploadResult` 的字段，但不暴露 `source_uri`，确保 API 输出只包含 dataset ID 和 Profile。

- [ ] **Step 3: Run the focused tests and verify the green result**

Run: `pytest tests/datasets/test_models.py tests/config/test_settings_contract.py tests/api/test_schemas.py -q`

Expected: PASS, with no API key or network access required.

- [ ] **Step 4: Commit the contracts**

```bash
git add pyproject.toml requirements.txt src/data_analysis_agent/datasets src/data_analysis_agent/config/settings.py src/data_analysis_agent/api/schemas.py src/data_analysis_agent/__init__.py tests/datasets tests/config/test_settings_contract.py tests/api/test_schemas.py
git commit -m "feat: add dataset upload contracts and settings"
```

### Task 2: 受控本地存储后端

**Files:**
- Create: `src/data_analysis_agent/datasets/storage.py`
- Modify: `src/data_analysis_agent/datasets/__init__.py`
- Create: `tests/datasets/test_storage.py`

**Interfaces:**
- Consumes `StoredObject`, `StorageError`, `DatasetErrorCode` from Task 1.
- Produces `StorageBackend` and `LocalStorageBackend`:

```python
class StorageBackend(Protocol):
    def put_stream(self, stream: BinaryIO, *, key: str, max_bytes: int) -> StoredObject: ...
    def open(self, uri: str) -> BinaryIO: ...
    def delete(self, uri: str) -> None: ...


class LocalStorageBackend:
    def __init__(self, root: str | Path): ...
```

- [ ] **Step 1: Write failing path-boundary and streaming tests**

```python
# tests/datasets/test_storage.py
from io import BytesIO

import pytest

from data_analysis_agent.datasets.errors import DatasetErrorCode, StorageError
from data_analysis_agent.datasets.storage import LocalStorageBackend


def test_local_backend_writes_reads_and_deletes_only_generated_relative_keys(tmp_path):
    storage = LocalStorageBackend(tmp_path)

    stored = storage.put_stream(
        BytesIO(b"name,value\nA,1\n"),
        key="datasets/11111111-1111-1111-1111-111111111111.csv",
        max_bytes=1024,
    )

    assert stored.uri == "local://datasets/11111111-1111-1111-1111-111111111111.csv"
    assert storage.open(stored.uri).read() == b"name,value\nA,1\n"
    storage.delete(stored.uri)
    with pytest.raises(StorageError):
        storage.open(stored.uri)


@pytest.mark.parametrize("key", ["../escape.csv", "/absolute.csv", "datasets/../escape.csv"])
def test_local_backend_rejects_path_traversal_keys(tmp_path, key):
    storage = LocalStorageBackend(tmp_path)

    with pytest.raises(StorageError, match="path"):
        storage.put_stream(BytesIO(b"x\n1\n"), key=key, max_bytes=1024)


def test_local_backend_removes_partial_file_when_stream_exceeds_limit(tmp_path):
    storage = LocalStorageBackend(tmp_path)

    with pytest.raises(StorageError) as exc_info:
        storage.put_stream(BytesIO(b"0123456789"), key="datasets/a.csv", max_bytes=5)

    assert exc_info.value.code is DatasetErrorCode.FILE_TOO_LARGE
    assert not (tmp_path / "datasets" / "a.csv").exists()
```

Run: `pytest tests/datasets/test_storage.py -q`

Expected: FAIL because `LocalStorageBackend` is not defined.

- [ ] **Step 2: Implement URI mapping and safe streaming**

Use only `local://` URIs. Resolve every candidate with `Path.resolve(strict=False)` and require `candidate.relative_to(root)` to succeed. Reject absolute keys, `..` path components, unknown URI schemes, symlink targets outside `root`, and non-positive limits. Write in 64 KiB chunks, update SHA-256, and remove the destination on any exception. Return checksums in the form `sha256:<lowercase-hex>`.

The storage implementation must never use the caller’s original filename to form a path; callers supply the UUID-generated key.

- [ ] **Step 3: Run storage tests and a compile check**

Run: `pytest tests/datasets/test_storage.py -q; python -m compileall -q src/data_analysis_agent/datasets`

Expected: PASS and exit code 0.

- [ ] **Step 4: Commit the storage backend**

```bash
git add src/data_analysis_agent/datasets/storage.py src/data_analysis_agent/datasets/__init__.py tests/datasets/test_storage.py
git commit -m "feat: add safe local dataset storage"
```

### Task 3: CSV 检查器和 DatasetProfile 生成

**Files:**
- Create: `src/data_analysis_agent/datasets/inspection.py`
- Modify: `src/data_analysis_agent/datasets/__init__.py`
- Create: `tests/datasets/test_inspection.py`

**Interfaces:**
- Consumes `DatasetProfile`, `ColumnProfile`, `SensitiveField` and validation errors.
- Produces:

```python
class CsvInspector:
    def inspect(self, stream: BinaryIO, *, filename: str) -> DatasetProfile: ...
```

- [ ] **Step 1: Write failing tests for encoding, delimiter and profile behavior**

```python
# tests/datasets/test_inspection.py
from io import BytesIO

import pytest

from data_analysis_agent.datasets.errors import DatasetErrorCode, UploadValidationError
from data_analysis_agent.datasets.inspection import CsvInspector


def inspect_bytes(payload: bytes, filename: str = "sample.csv"):
    return CsvInspector().inspect(BytesIO(payload), filename=filename)


@pytest.mark.parametrize(
    ("delimiter", "raw_delimiter"),
    [(",", b","), (";", b";"), ("\t", b"\t"), ("|", b"|")],
)
def test_inspector_recognizes_supported_delimiters(delimiter, raw_delimiter):
    profile = inspect_bytes(b"name" + raw_delimiter + b"value\nA" + raw_delimiter + b"1\n")

    assert profile.delimiter == delimiter
    assert profile.row_count == 1
    assert profile.column_count == 2


def test_inspector_detects_gbk_and_generates_schema_preview_and_missing_counts():
    payload = "姓名,年龄,邮箱\n张三,18,zhang@example.com\n李四,,li@example.com\n".encode("gbk")

    profile = inspect_bytes(payload)

    assert profile.encoding.lower() in {"gbk", "gb18030", "cp936"}
    assert profile.row_count == 2
    assert profile.preview_rows[0]["邮箱"] == "[REDACTED]"
    age = next(column for column in profile.columns if column.name == "年龄")
    assert age.missing_count == 1
    assert age.missing_rate == 0.5
    assert any(field.column_name == "邮箱" for field in profile.sensitive_fields)


def test_inspector_limits_preview_to_twenty_rows():
    payload = ("id,value\n" + "".join(f"{i},{i}\n" for i in range(25))).encode()

    profile = inspect_bytes(payload)

    assert len(profile.preview_rows) == 20
    assert profile.row_count == 25


def test_inspector_prioritizes_utf8_bom():
    profile = inspect_bytes("name,value\n甲,1\n".encode("utf-8-sig"))

    assert profile.encoding.lower().replace("_", "-") in {"utf-8-sig", "utf-8"}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"", DatasetErrorCode.EMPTY_FILE),
        (b"a,a\n1,2\n", DatasetErrorCode.DUPLICATE_COLUMNS),
        (b",value\n1,2\n", DatasetErrorCode.EMPTY_COLUMN_NAME),
        (b"a,b\n1\n", DatasetErrorCode.INVALID_CSV),
        (b"a\x00,b\n1,2\n", DatasetErrorCode.CONTROL_CHARACTER),
    ],
)
def test_inspector_rejects_structural_errors(payload, code):
    with pytest.raises(UploadValidationError) as exc_info:
        inspect_bytes(payload)

    assert exc_info.value.code is code


def test_inspector_rejects_non_csv_extension_without_reading_as_valid_data():
    with pytest.raises(UploadValidationError) as exc_info:
        inspect_bytes(b"a,b\n1,2\n", filename="sample.xlsx")

    assert exc_info.value.code is DatasetErrorCode.UNSUPPORTED_EXTENSION
```

Run: `pytest tests/datasets/test_inspection.py -q`

Expected: FAIL because `CsvInspector` is not defined.

- [ ] **Step 2: Implement strict decoding and CSV parsing**

Implement these exact rules:

1. Normalize the display filename to its final component after converting `\\` to `/`; reject blank names and require `.csv` case-insensitively.
2. Read the bounded stream from the beginning. Detect UTF-8/UTF-16 BOMs before calling `charset_normalizer.from_bytes`. Use the best match only when it has a decodable result and confidence `>= 0.50`; otherwise try the explicit Chinese fallback candidates `gb18030` and `gbk` only when their decoded text contains no replacement character. If no candidate is reliable, raise `ENCODING_DETECTION_FAILED`.
3. Reject NUL and control characters other than `\t`, `\r`, and `\n` before parsing.
4. Run `csv.Sniffer().sniff(sample, delimiters=",;\t|")`. For a valid one-column CSV with no delimiter, use `,`; otherwise require a supported delimiter and parse with `csv.reader(..., strict=True)`.
5. Strip header whitespace for validation and output. Reject empty or duplicate names. Require every data row to have exactly the header width.
6. Build a pandas DataFrame from parsed rows without re-reading the path. Treat blank strings as missing, infer one of `empty`, `boolean`, `integer`, `number`, `datetime`, `string`, `mixed`, and calculate non-null/missing counts and rates.
7. Inspect only up to 20 non-blank values per column for sensitivity. Match column names containing email/邮箱, phone/手机/mobile/tel, id card/身份证/identity/ssn; match email, Chinese mobile number, and Chinese ID patterns. Deduplicate `detected_by` values and redact every sensitive preview cell as `[REDACTED]`.
8. Convert preview values to JSON-native `None`, `bool`, `int`, `float`, or `str`; convert non-finite floats to `None`.

- [ ] **Step 3: Run the inspector tests and verify the module boundary**

Run: `pytest tests/datasets/test_inspection.py tests/datasets/test_models.py -q`

Expected: PASS; the tests must not create or read any path outside the in-memory `BytesIO` payload.

- [ ] **Step 4: Commit the CSV inspector**

```bash
git add src/data_analysis_agent/datasets/inspection.py src/data_analysis_agent/datasets/__init__.py tests/datasets/test_inspection.py
git commit -m "feat: inspect and profile uploaded CSV files"
```

### Task 4: 上传编排、元数据持久化和归属解析

**Files:**
- Create: `src/data_analysis_agent/datasets/service.py`
- Create: `src/data_analysis_agent/datasets/resolver.py`
- Modify: `src/data_analysis_agent/datasets/__init__.py`
- Create: `tests/datasets/conftest.py`
- Create: `tests/datasets/test_upload_service.py`
- Create: `tests/datasets/test_resolver.py`

**Interfaces:**
- Consumes `StorageBackend`, `CsvInspector`, `DatasetUploadResult`, `DatasetRecord`, `UnitOfWork`.
- Produces:

```python
class DatasetMetadataStore(Protocol):
    def create(self, record: DatasetRecord) -> DatasetRecord: ...
    def get_for_user(self, dataset_id: UUID, user_id: UUID) -> DatasetRecord | None: ...


class UnitOfWorkDatasetStore:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]): ...


class InMemoryDatasetStore:
    """Offline quick_analysis adapter; it stores metadata only for one process."""


class DatasetUploadService:
    def __init__(self, *, storage, inspector, metadata_store, max_upload_size: int): ...
    def upload(self, stream: BinaryIO, *, original_filename: str, owner_id: UUID) -> DatasetUploadResult: ...


class DatasetResolver:
    def __init__(self, *, storage: StorageBackend, metadata_store: DatasetMetadataStore): ...
    def open_for_user(self, dataset_id: UUID, *, owner_id: UUID) -> BinaryIO: ...
    def profile_for_user(self, dataset_id: UUID, *, owner_id: UUID) -> DatasetProfile: ...
```

- [ ] **Step 1: Write failing service, rollback and access-control tests**

```python
# tests/datasets/test_upload_service.py
from io import BytesIO
from uuid import uuid4

import pytest

from data_analysis_agent.datasets.errors import DatasetAccessDeniedError, UploadValidationError
from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.service import DatasetUploadService, UnitOfWorkDatasetStore
from data_analysis_agent.datasets.storage import LocalStorageBackend


def test_upload_persists_only_metadata_and_returns_opaque_dataset_id(tmp_path, uow_factory):
    owner_id = uuid4()
    service = DatasetUploadService(
        storage=LocalStorageBackend(tmp_path / "objects"),
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )

    result = service.upload(
        BytesIO("name,value\nA,1\n".encode()),
        original_filename="../../sales.csv",
        owner_id=owner_id,
    )

    assert result.dataset_id
    assert result.original_filename == "sales.csv"
    with uow_factory() as uow:
        record = uow.datasets.get_for_user(result.dataset_id, owner_id)
        assert record is not None
        assert record.source_uri.startswith("local://")
        assert record.metadata_json["profile"]["row_count"] == 1


def test_invalid_upload_does_not_create_metadata_or_object(tmp_path, uow_factory):
    owner_id = uuid4()
    storage = LocalStorageBackend(tmp_path / "objects")
    service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=1024,
    )

    with pytest.raises(UploadValidationError):
        service.upload(BytesIO(b"a,a\n1,2\n"), original_filename="bad.csv", owner_id=owner_id)

    with uow_factory() as uow:
        assert uow.datasets.list_for_user(owner_id) == []
    assert list((tmp_path / "objects").rglob("*")) == []
```

```python
# tests/datasets/test_resolver.py
from uuid import uuid4

import pytest

from data_analysis_agent.datasets.errors import DatasetAccessDeniedError


def test_resolver_rejects_a_dataset_owned_by_another_user(uploaded_dataset):
    resolver, dataset_id, owner_id = uploaded_dataset

    with pytest.raises(DatasetAccessDeniedError):
        resolver.open_for_user(dataset_id, owner_id=uuid4())

    assert resolver.profile_for_user(dataset_id, owner_id=owner_id).row_count == 1
```

The fixture `uploaded_dataset` must upload `name,value\nA,1\n` through the real service, return `(resolver, dataset_id, owner_id)`, and use a temporary SQLite database plus `LocalStorageBackend`; it must not use a mock for the storage read.

Run: `pytest tests/datasets/test_upload_service.py tests/datasets/test_resolver.py -q`

Expected: FAIL because the metadata store, upload service and resolver do not exist.

- [ ] **Step 2: Implement metadata stores and upload transaction**

`UnitOfWorkDatasetStore.create()` must ensure the owner row, add the `DatasetRecord`, commit once, and re-raise `TransactionError` as `DatasetPersistenceError`. `get_for_user()` must call the existing repository method and return no record for another owner.

`InMemoryDatasetStore` must keep a `dict[UUID, DatasetRecord]` and implement the same methods; it is used only by offline `quick_analysis` and never writes file bytes to the database.

`DatasetUploadService.upload()` must:

1. Validate the display filename and extension through the inspector boundary.
2. Copy the input stream into a `SpooledTemporaryFile` in 64 KiB chunks, aborting when the actual byte count exceeds `max_upload_size`.
3. Rewind and call `CsvInspector.inspect()`.
4. Generate `dataset_id = uuid4()` and key `datasets/{dataset_id}.csv`; rewind and call `StorageBackend.put_stream()`.
5. Build `DatasetRecord` with `user_id`, safe display name, `stored.uri`, `text/csv`, actual size, checksum, `utc_now()`, and `metadata_json={"profile": profile.model_dump(mode="json"), "original_filename": safe_name}`.
6. Call `metadata_store.create(record)`.
7. On any persistence error after object creation, call `storage.delete(stored.uri)` before raising `DatasetPersistenceError`; preserve the original error as `__cause__`.
8. Return `DatasetUploadResult` without `source_uri`.

Do not log the original filename, URI, or stream content in exception messages. Repeated identical uploads receive separate UUIDs; their checksum fields can match.

- [ ] **Step 3: Implement the resolver and run focused integration tests**

`DatasetResolver.open_for_user()` must fetch the record by owner, raise `DatasetAccessDeniedError` for missing/cross-owner records, and then delegate the stored URI to `StorageBackend.open()`. `profile_for_user()` must load only `record.metadata_json["profile"]` into `DatasetProfile`, raising `DatasetPersistenceError` when the persisted JSON is malformed.

Run: `pytest tests/datasets tests/database/test_file_metadata_boundary.py -q`

Expected: PASS; the database tests must still prove that persistence never reads or copies file bytes.

- [ ] **Step 4: Commit upload and resolver behavior**

```bash
git add src/data_analysis_agent/datasets tests/datasets
git commit -m "feat: persist and resolve validated datasets"
```

### Task 5: Agent dataset ID 接入和 `quick_analysis` 兼容适配

**Files:**
- Modify: `src/data_analysis_agent/agent/core.py`
- Modify: `src/data_analysis_agent/agent/prompts.py`
- Modify: `src/data_analysis_agent/cli.py`
- Modify: `src/data_analysis_agent/agent/__init__.py`
- Modify: `src/data_analysis_agent/__init__.py`
- Modify: `tests/fixtures/fake_llm.py`
- Modify: `tests/integration/test_analysis_flow.py`
- Modify: `tests/public_api/test_public_api_contract.py`
- Create: `tests/integration/test_dataset_analysis_flow.py`
- Modify: `tests/contract/test_agent_contract.py`

**Interfaces:**
- Consumes `DatasetUploadService`, `DatasetResolver`, `InMemoryDatasetStore`, `LocalStorageBackend`, `CsvInspector`.
- `DataAnalysisAgent.__init__` adds optional `dataset_resolver: DatasetResolver | None` and `dataset_owner_id: UUID | None`.
- `DataAnalysisAgent.analyze()` accepts `dataset_ids: Sequence[UUID | str] | None` and keeps `files` only as a deprecated internal parameter; `quick_analysis` performs the file conversion before invoking it.
- `quick_analysis()` has this final signature:

```python
def quick_analysis(
    query: str,
    files: Sequence[str] | None = None,
    *,
    dataset_ids: Sequence[UUID | str] | None = None,
    output_dir: str | Path | None = None,
    max_rounds: int | None = None,
    generate_word_report: bool | None = None,
    settings: Settings | None = None,
    dataset_resolver: DatasetResolver | None = None,
    dataset_owner_id: UUID | None = None,
) -> dict[str, Any]: ...
```

- [ ] **Step 1: Write failing no-path-leak and loader tests**

```python
# tests/integration/test_dataset_analysis_flow.py
from pathlib import Path
from uuid import UUID

from tests.fixtures.fake_llm import FakeLLM, yaml_response


def test_quick_analysis_uses_dataset_id_and_loader_instead_of_input_path(tmp_path, monkeypatch):
    source = tmp_path / "private.csv"
    source.write_text("name,value\nA,1\n", encoding="utf-8")
    seen = {}

    def response(fake_llm, call):
        dataset_id = call.prompt.split("dataset_id=")[1].split()[0]
        seen["dataset_id"] = UUID(dataset_id)
        return yaml_response("generate_code", code=f"df = load_dataset('{dataset_id}')")

    fake_llm = FakeLLM([response, yaml_response("analysis_complete", final_report="# done"), yaml_response("analysis_complete", final_report="# done")])
    monkeypatch.setattr("data_analysis_agent.agent.core.LLMHelper", lambda config: fake_llm)

    from data_analysis_agent import quick_analysis

    result = quick_analysis(
        "分析私有文件",
        files=[str(source)],
        output_dir=tmp_path / "outputs",
        max_rounds=2,
        generate_word_report=False,
    )

    assert result["final_report"] == "# done"
    assert str(source) not in "\n".join(call.prompt for call in fake_llm.calls)
    assert seen["dataset_id"]
```

```python
# tests/public_api/test_public_api_contract.py
def test_quick_analysis_exposes_dataset_ids_without_removing_files():
    parameters = inspect.signature(data_analysis_agent.quick_analysis).parameters

    assert "files" in parameters
    assert "dataset_ids" in parameters
    assert parameters["dataset_ids"].kind is inspect.Parameter.KEYWORD_ONLY
```

Run: `pytest tests/integration/test_dataset_analysis_flow.py tests/public_api/test_public_api_contract.py -q`

Expected: FAIL because the prompt still contains paths and `load_dataset`/`dataset_ids` are not wired.

- [ ] **Step 2: Implement the ID-only Agent context**

In `DataAnalysisAgent`, normalize incoming IDs with `UUID(str(value))`. Require a resolver and owner ID whenever `dataset_ids` is non-empty. Before the first model call:

```python
for dataset_id in dataset_ids:
    profile = self.dataset_resolver.profile_for_user(
        dataset_id, owner_id=self.dataset_owner_id
    )
    dataset_context.append({"dataset_id": str(dataset_id), "profile": profile.model_dump(mode="json")})

def load_dataset(dataset_id: str):
    with self.dataset_resolver.open_for_user(
        UUID(str(dataset_id)), owner_id=self.dataset_owner_id
    ) as stream:
        return pandas.read_csv(stream)

self.executor.set_variable("load_dataset", load_dataset)
self.executor.set_variable("dataset_ids", tuple(str(item) for item in dataset_ids))
```

The initial user prompt must contain only `dataset_id=<uuid>` and a serialized, redacted profile. It must not interpolate `DatasetRecord.source_uri`, `Path`, `files`, or `original_filename` into the prompt. The executor callback is the only place that opens the URI.

Update `data_analysis_system_prompt` to replace direct file-path loading and encoding guesses with:

```text
数据集只能通过 dataset_id 和 load_dataset(dataset_id) 加载。
不要请求、打印或猜测 source_uri、绝对路径或用户原始文件名。
编码、分隔符、列名、类型和缺失统计已经由后端校验并提供。
```

Keep chart output paths unchanged; the no-path-leak rule applies to uploaded input data, not the agent’s own session output directory.

- [ ] **Step 3: Implement `quick_analysis` file compatibility and explicit dataset IDs**

When `files` is supplied, open each path in the CLI/API adapter, create a `LocalStorageBackend(settings.storage_local_root)`, `CsvInspector`, `InMemoryDatasetStore`, and `DatasetUploadService`, and upload each file under a process-local owner UUID. Pass the resulting IDs, resolver, and owner ID to `DataAnalysisAgent`. Never pass the input path to `analyze()`.

When `dataset_ids` is supplied, require both `dataset_resolver` and `dataset_owner_id`; otherwise raise `DatasetAccessDeniedError` with a message that names the missing dependency but does not echo a path. Reject passing both `files` and `dataset_ids` in one call with `UploadValidationError(INVALID_DATASET_REQUEST, ...)`; add `INVALID_DATASET_REQUEST` to the error enum.

Add CLI `--dataset-id` with `action="append"`. Reject combining positional files and `--dataset-id`, and forward IDs without resolving or printing their source URI. Keep the existing missing local-file check for positional files.

- [ ] **Step 4: Update offline fixtures and existing flow assertions**

Add `dataset_id_from_prompt()` to `tests/fixtures/fake_llm.py`. Change integration fake code from:

```python
df = pd.read_csv("<absolute fixture path>")
```

to a callable fake response that extracts the ID and returns:

```python
df = load_dataset("<dataset id from prompt>")
```

Replace the old assertion that the input path appears in the first prompt with assertions that the dataset ID appears and the input path does not. Preserve all existing assertions for executor failure feedback, chart generation, Markdown, Word fallback, and max-round behavior.

- [ ] **Step 5: Run Agent contract and integration tests**

Run: `pytest tests/contract/test_agent_contract.py tests/integration/test_analysis_flow.py tests/integration/test_dataset_analysis_flow.py tests/public_api/test_public_api_contract.py -q`

Expected: PASS with no real LLM calls. A failure mentioning the input path in any `FakeLLMCall.prompt` is a security regression.

- [ ] **Step 6: Commit Agent integration**

```bash
git add src/data_analysis_agent/agent src/data_analysis_agent/cli.py src/data_analysis_agent/__init__.py tests/fixtures/fake_llm.py tests/integration tests/public_api/test_public_api_contract.py tests/contract/test_agent_contract.py
git commit -m "feat: route analysis through dataset ids"
```

### Task 6: 文档、完整回归与交付验证

**Files:**
- Modify: `README.md`
- Modify: `tests/packaging/test_package_contract.py` if dependency/config contract assertions need updating
- Create: `tests/datasets/test_end_to_end_contract.py`

**Interfaces:**
- Consumes all M04 public contracts from Tasks 1–5.
- Produces documented upload and ID-based analysis examples plus a repeatable offline acceptance suite.

- [ ] **Step 1: Write the end-to-end acceptance test**

```python
# tests/datasets/test_end_to_end_contract.py
from io import BytesIO
from uuid import uuid4

from data_analysis_agent.datasets.inspection import CsvInspector
from data_analysis_agent.datasets.service import DatasetUploadService, UnitOfWorkDatasetStore
from data_analysis_agent.datasets.storage import LocalStorageBackend


def test_upload_acceptance_returns_preview_schema_counts_and_missing_values(tmp_path, uow_factory):
    owner_id = uuid4()
    service = DatasetUploadService(
        storage=LocalStorageBackend(tmp_path / "objects"),
        inspector=CsvInspector(),
        metadata_store=UnitOfWorkDatasetStore(uow_factory),
        max_upload_size=4096,
    )

    result = service.upload(
        BytesIO(b"name,value\nA,1\nB,\n"),
        original_filename="sample.csv",
        owner_id=owner_id,
    )

    assert result.profile.row_count == 2
    assert [column.name for column in result.profile.columns] == ["name", "value"]
    assert next(column for column in result.profile.columns if column.name == "value").missing_count == 1
    assert len(result.profile.preview_rows) == 2
    assert result.dataset_id
```

Run: `pytest tests/datasets/test_end_to_end_contract.py -q`

Expected: PASS after Tasks 1–4; keep the test in the final suite.

- [ ] **Step 2: Update README without exposing implementation paths**

Document `DatasetUploadService`, `dataset_id`, `STORAGE_LOCAL_ROOT`, `MAX_UPLOAD_SIZE`, and the two supported calls:

```python
report = quick_analysis(query="分析数据", files=["sales.csv"])
report = quick_analysis(query="分析已上传数据", dataset_ids=[dataset_id])
```

State that the compatibility `files` adapter validates and stores the input before analysis, while model prompts contain only dataset IDs and redacted Profile information. Do not document a real API key, local storage secret, absolute upload path, or an example that passes a user path into a prompt.

- [ ] **Step 3: Run all verification commands**

Run each command separately and record the result:

```bash
pytest -q
python -m pip install -e .
python -m pip check
python -m compileall -q src
python -m pytest tests/datasets tests/integration tests/contract tests/public_api -q
```

Expected: all tests pass, editable install succeeds, `pip check` reports no broken requirements, and compileall exits successfully. No command may require `OPENAI_API_KEY`, a running MySQL instance, an object-storage endpoint, or network access.

- [ ] **Step 4: Review the final diff and commit documentation**

```bash
git diff --check
git status --short
git diff --stat codex/m03-database-persistence...HEAD
git add README.md tests/datasets/test_end_to_end_contract.py tests/packaging/test_package_contract.py
git commit -m "docs: document dataset upload and validation flow"
```

Confirm with `git status --short --branch` that the worktree is clean before handoff.

## Spec Coverage Self-Review

- Storage abstraction, local staging, UUID keys and path-boundary checks: Tasks 2 and 4.
- File-like upload boundary and extension/size checks: Tasks 2–4.
- BOM/`charset-normalizer`, four delimiters, strict CSV structure: Task 3.
- Empty files, duplicate/empty columns, inconsistent rows and control characters: Task 3.
- Preview limit, schema, row counts and missing statistics: Tasks 3 and 6.
- Column-name/value-pattern sensitive-field detection and redacted preview: Task 3.
- Dataset Profile JSON in existing `metadata_json`: Task 4.
- Rollback and object cleanup: Task 4.
- Owner isolation and ID-only resolver: Tasks 4 and 5.
- `quick_analysis` compatibility and no input-path prompt leakage: Task 5.
- Offline/no-real-API verification and packaging: Task 6.
- Excel parsing, async queue, chunk upload and database file bodies remain explicitly out of scope.

## Handoff

After this plan is approved, execute it in the isolated worktree `codex/m04-file-upload-validation`. Use one fresh review checkpoint after each task. The implementation worker must run each RED test before writing its corresponding production code and must not mark a task complete without the focused test command and commit succeeding.
