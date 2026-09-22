from uuid import uuid4

import pytest

from data_analysis_agent.domain.enums import ToolCallStatus
from data_analysis_agent.tools.audit import InMemoryToolCallRecorder, ToolAuditRecord


def test_memory_recorder_redacts_secret_and_keeps_snapshot():
    recorder = InMemoryToolCallRecorder()
    arguments = {
        "dataset_id": "ds-1",
        "api_key": "do-not-store",
        "nested": {
            "Authorization": "Bearer private",
            "values": ["safe", {"password": "hidden"}],
        },
    }
    record = ToolAuditRecord(
        call_id=uuid4(),
        task_id=uuid4(),
        tool_name="inspect_dataset",
        status=ToolCallStatus.SUCCEEDED,
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00",
        duration_ms=10,
        arguments=arguments,
        output={"row_count": 2},
    )

    recorder.record(record)
    arguments["nested"]["values"].append("changed")

    assert recorder.records[0].arguments["api_key"] == "[REDACTED]"
    assert recorder.records[0].arguments["nested"]["Authorization"] == "[REDACTED]"
    assert recorder.records[0].arguments["nested"]["values"][1]["password"] == "[REDACTED]"
    assert recorder.records[0].arguments["nested"]["values"] == (
        "safe",
        {"password": "[REDACTED]"},
    )
    assert "do-not-store" not in recorder.records[0].model_dump_json()
    assert recorder.records == tuple(recorder.records)


def test_audit_snapshot_caps_strings_mappings_and_sequences():
    record = ToolAuditRecord(
        call_id=uuid4(),
        task_id=uuid4(),
        tool_name="inspect_dataset",
        status=ToolCallStatus.FAILED,
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00",
        duration_ms=10,
        arguments={
            f"key-{index}": "x" * 300
            for index in range(25)
        },
        output=list(range(25)),
    )
    recorder = InMemoryToolCallRecorder()
    recorder.record(record)

    snapshot = recorder.records[0]
    assert len(snapshot.arguments) == 20
    assert all(len(value) == 256 for value in snapshot.arguments.values())
    assert snapshot.output == tuple(range(20))


def test_audit_snapshot_rejects_in_place_mapping_merge():
    record = ToolAuditRecord(
        call_id=uuid4(),
        task_id=uuid4(),
        tool_name="inspect_dataset",
        status=ToolCallStatus.SUCCEEDED,
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00",
        duration_ms=10,
        arguments={"dataset_id": "ds-1"},
    )

    with pytest.raises(TypeError, match="audit snapshot is immutable"):
        record.arguments.__ior__({"unexpected": "value"})

    assert record.arguments == {"dataset_id": "ds-1"}


def test_audit_snapshot_redacts_storage_paths_and_uris():
    record = ToolAuditRecord(
        call_id=uuid4(),
        task_id=uuid4(),
        tool_name="inspect_dataset",
        status=ToolCallStatus.SUCCEEDED,
        started_at="2026-09-21T12:00:00+00:00",
        finished_at="2026-09-21T12:00:00.010000+00:00",
        duration_ms=10,
        arguments={
            "file_path": "C:/private/uploads/sales.csv",
            "source_uri": "local://datasets/ds-1/original.csv",
            "nested": {"storage_uri": "s3://private-bucket/secret.csv"},
        },
        output={
            "file_path": "C:/private/outputs/chart.png",
            "source_uri": "s3://private-bucket/chart.png",
        },
    )

    payload = record.model_dump_json()

    assert record.arguments["file_path"] == "[REDACTED]"
    assert record.arguments["source_uri"] == "[REDACTED]"
    assert record.arguments["nested"]["storage_uri"] == "[REDACTED]"
    assert record.output["file_path"] == "[REDACTED]"
    assert record.output["source_uri"] == "[REDACTED]"
    assert "C:/private" not in payload
    assert "s3://private-bucket" not in payload
