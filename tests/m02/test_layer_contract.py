import json
from datetime import datetime, timezone
from uuid import uuid4

from data_analysis_agent.api import AnalysisTaskResponse
from data_analysis_agent.domain import AnalysisTask, TaskStatus
from data_analysis_agent.persistence import AnalysisTaskRecord, task_to_record


def test_domain_persistence_and_api_models_are_distinct_layers():
    task = AnalysisTask(task_id=uuid4(), query="分析", status=TaskStatus.ANALYZING)
    record = task_to_record(task)
    response = AnalysisTaskResponse(
        task_id=task.task_id,
        query=task.query,
        status=TaskStatus.ANALYZING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    assert type(task) is not type(record)
    assert type(task) is not type(response)
    assert isinstance(record, AnalysisTaskRecord)
    assert response.status is TaskStatus.ANALYZING
    assert TaskStatus.__module__ == "data_analysis_agent.domain.enums"


def test_all_three_boundaries_are_json_serializable():
    task = AnalysisTask(query="分析")
    record = task_to_record(task)
    response = AnalysisTaskResponse(
        task_id=task.task_id,
        query=task.query,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )

    for model in (task, record, response):
        assert isinstance(json.loads(model.model_dump_json()), dict)
