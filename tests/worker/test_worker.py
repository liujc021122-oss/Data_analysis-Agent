from datetime import timedelta
from threading import Event, Thread
from time import sleep
from uuid import uuid4

from data_analysis_agent.api.schemas import AnalysisTaskCreateRequest
from data_analysis_agent.domain.enums import TaskStatus
from data_analysis_agent.domain.models import utc_now
from data_analysis_agent.services.persistence import TaskPersistenceService
from data_analysis_agent.worker import (
    AnalysisTaskWorker,
    InMemoryTaskBroker,
    RetryableTaskError,
    TaskSubmissionService,
)


def _request(key: str):
    return AnalysisTaskCreateRequest(query="分析样例", idempotency_key=key)


def _submitted(uow_factory, broker, key="task"):
    persistence = TaskPersistenceService(uow_factory)
    submission = TaskSubmissionService(persistence=persistence, broker=broker)
    result = submission.submit(user_id=uuid4(), request=_request(key))
    return persistence, result.task


class SuccessfulAgent:
    def __init__(self, calls):
        self.calls = calls

    def run(self):
        self.calls.append("run")
        return {"status": TaskStatus.COMPLETED}


def test_worker_claims_task_once_and_records_stage_times(uow_factory):
    broker = InMemoryTaskBroker()
    persistence, task = _submitted(uow_factory, broker)
    calls = []
    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: SuccessfulAgent(calls),
    )

    first = worker.process(task.task_id)
    second = worker.process(task.task_id)

    assert first.executed is True
    assert second.executed is False
    assert calls == ["run"]
    with uow_factory() as uow:
        stored = uow.tasks.get(task.task_id)
        events = uow.task_events.list_for_task(task.task_id)
    assert stored.status is TaskStatus.COMPLETED
    stage_events = [event for event in events if event.to_status is not TaskStatus.PENDING]
    assert [event.to_status for event in stage_events] == [
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.EXPLORING,
        TaskStatus.CLEANING,
        TaskStatus.ANALYZING,
        TaskStatus.VALIDATING,
        TaskStatus.REPORTING,
        TaskStatus.COMPLETED,
    ]
    for event in stage_events[1:]:
        assert "phase_started_at" in event.metadata
        assert "phase_finished_at" in event.metadata


class FlakyAgent:
    def __init__(self, calls):
        self.calls = calls

    def run(self):
        self.calls.append("run")
        if len(self.calls) == 1:
            raise RetryableTaskError("temporary worker failure")
        return {"status": TaskStatus.COMPLETED}


def test_retryable_failure_is_requeued_and_then_completes(uow_factory):
    broker = InMemoryTaskBroker()
    persistence, task = _submitted(uow_factory, broker, "retry")
    calls = []
    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: FlakyAgent(calls),
        max_retries=2,
        retry_backoff_seconds=0,
    )

    first = worker.process(task.task_id)
    second = worker.process(task.task_id)

    assert first.retry_scheduled is True
    assert second.status is TaskStatus.COMPLETED
    assert calls == ["run", "run"]
    with uow_factory() as uow:
        stored = uow.tasks.get(task.task_id)
        assert stored.status is TaskStatus.COMPLETED
        assert stored.metadata["worker_attempt"] == 1


def test_retry_exhaustion_marks_task_failed(uow_factory):
    broker = InMemoryTaskBroker()
    persistence, task = _submitted(uow_factory, broker, "retry-exhausted")

    def always_fails(task, **kwargs):
        class Agent:
            def run(self):
                raise RetryableTaskError("still unavailable")

        return Agent()

    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=always_fails,
        max_retries=1,
        retry_backoff_seconds=0,
    )

    worker.process(task.task_id)
    final = worker.process(task.task_id)

    assert final.status is TaskStatus.FAILED
    with uow_factory() as uow:
        stored = uow.tasks.get(task.task_id)
    assert stored.status is TaskStatus.FAILED
    assert stored.error_code == "WORKER_RETRY_EXHAUSTED"


def test_cancelled_task_does_not_construct_agent(uow_factory):
    broker = InMemoryTaskBroker()
    persistence, task = _submitted(uow_factory, broker, "already-cancelled")
    persistence.cancel_task(task.task_id)
    constructed = []

    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: constructed.append(task),
    )

    result = worker.process(task.task_id)

    assert result.executed is False
    assert constructed == []


def test_stale_running_task_is_requeued_after_worker_restart(uow_factory):
    broker = InMemoryTaskBroker()
    persistence, task = _submitted(uow_factory, broker, "stale")
    persistence.claim_task(task.task_id)
    with uow_factory() as uow:
        row = uow.session.get(__import__(
            "data_analysis_agent.persistence.orm_models",
            fromlist=["AnalysisTaskORM"],
        ).AnalysisTaskORM, task.task_id)
        row.updated_at = utc_now() - timedelta(hours=2)
        uow.commit()

    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: SuccessfulAgent([]),
        stale_after_seconds=60,
    )
    recovered = worker.recover_stale()

    assert recovered == [task.task_id]
    with uow_factory() as uow:
        assert uow.tasks.get(task.task_id).status is TaskStatus.QUEUED
    assert broker.messages[-1].task_id == task.task_id


def test_stale_pending_task_is_requeued_after_submitter_crash(uow_factory):
    broker = InMemoryTaskBroker()
    persistence = TaskPersistenceService(uow_factory)
    task = persistence.create_task(
        user_id=uuid4(),
        request=_request("stale-pending"),
    )
    with uow_factory() as uow:
        row = uow.session.get(
            __import__(
                "data_analysis_agent.persistence.orm_models",
                fromlist=["AnalysisTaskORM"],
            ).AnalysisTaskORM,
            task.task_id,
        )
        row.updated_at = utc_now() - timedelta(hours=2)
        uow.commit()

    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: SuccessfulAgent([]),
        stale_after_seconds=60,
    )

    recovered = worker.recover_stale()

    assert recovered == [task.task_id]
    assert broker.messages[-1].task_id == task.task_id
    with uow_factory() as uow:
        assert uow.tasks.get(task.task_id).status is TaskStatus.QUEUED


def test_stale_queued_task_is_requeued_after_publish_window_crash(uow_factory):
    broker = InMemoryTaskBroker()
    persistence = TaskPersistenceService(uow_factory)
    task = persistence.create_task(
        user_id=uuid4(),
        request=_request("stale-queued"),
    )
    queued, _ = persistence.transition_task(
        task_id=task.task_id,
        target=TaskStatus.QUEUED,
        message="task queued before broker publish",
    )
    with uow_factory() as uow:
        row = uow.session.get(
            __import__(
                "data_analysis_agent.persistence.orm_models",
                fromlist=["AnalysisTaskORM"],
            ).AnalysisTaskORM,
            queued.task_id,
        )
        row.updated_at = utc_now() - timedelta(hours=2)
        uow.commit()

    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: SuccessfulAgent([]),
        stale_after_seconds=60,
    )

    recovered = worker.recover_stale()

    assert recovered == [task.task_id]
    assert broker.messages[-1].task_id == task.task_id


class CancellableAgent:
    def __init__(self):
        self.started = Event()
        self.stopped = Event()

    def run(self):
        self.started.set()
        self.stopped.wait(timeout=5)
        return {"status": TaskStatus.CANCELLED}

    def cancel(self):
        self.stopped.set()


def test_active_cancellation_invokes_agent_stop_and_persists_cancelled(
    uow_factory,
):
    broker = InMemoryTaskBroker()
    persistence, task = _submitted(uow_factory, broker, "active-cancel")
    agent = CancellableAgent()
    worker = AnalysisTaskWorker(
        persistence=persistence,
        broker=broker,
        agent_factory=lambda task, **kwargs: agent,
    )
    result_holder = []
    thread = Thread(
        target=lambda: result_holder.append(worker.process(task.task_id)),
        daemon=True,
    )
    thread.start()
    assert agent.started.wait(timeout=2)

    worker.cancel(task.task_id)
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert agent.stopped.is_set()
    assert result_holder[0].status is TaskStatus.CANCELLED
