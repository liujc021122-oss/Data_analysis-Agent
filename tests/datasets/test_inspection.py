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


def test_inspector_preserves_non_bom_utf8_chinese_text_and_headers():
    profile = inspect_bytes("姓名,年龄\n张三,18\n".encode("utf-8"))

    assert profile.encoding.lower().replace("_", "-") == "utf-8"
    assert profile.preview_rows[0] == {"姓名": "张三", "年龄": "18"}


def test_inspector_rejects_low_confidence_charset_detection(monkeypatch):
    class LowConfidenceMatch:
        encoding = "utf-8"
        coherence = 0.49

        def __str__(self):
            return "name,value\nA,1\n"

    class Results:
        def best(self):
            return LowConfidenceMatch()

    monkeypatch.setattr("data_analysis_agent.datasets.inspection.from_bytes", lambda payload: Results())

    with pytest.raises(UploadValidationError) as exc_info:
        inspect_bytes(b"\x80")

    assert exc_info.value.code is DatasetErrorCode.ENCODING_DETECTION_FAILED


def test_inspector_rejects_blank_filename():
    with pytest.raises(UploadValidationError) as exc_info:
        inspect_bytes(b"name,value\nA,1\n", filename="")

    assert exc_info.value.code is DatasetErrorCode.UNSUPPORTED_EXTENSION


def test_inspector_accepts_valid_one_column_csv_when_sniffer_cannot_detect_delimiter():
    profile = inspect_bytes(b"name\nA\nB\n")

    assert profile.delimiter == ","
    assert profile.column_count == 1
    assert profile.row_count == 2


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
