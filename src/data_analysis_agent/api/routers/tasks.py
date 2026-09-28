from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from ...domain.enums import TaskStatus
from ...persistence.errors import EntityNotFoundError, IdempotencyConflictError, PersistenceError
from ..auth import Principal, get_current_principal
from ..errors import APIError
from ..pagination import PaginationParams
from ..schemas import (
    AnalysisTaskCreateRequest, AnalysisTaskResponse, ArtifactResponse,
    ErrorResponse, TaskEventListResponse, TaskEventResponse,
    TaskListResponse, TaskRetryResponse, TaskSubmissionResponse,
)

router = APIRouter(prefix="/analysis-tasks", tags=["analysis-tasks"])


def _services(request: Request):
    application = request.app.state.api_application
    if application.task_persistence is None:
        raise APIError("TASK_SERVICE_UNAVAILABLE", "task service is unavailable", status_code=503)
    if application.task_submission is None:
        raise APIError(
            "TASK_BROKER_NOT_CONFIGURED",
            "task broker is not configured",
            status_code=503,
        )
    return application.task_persistence, application.task_submission


def _not_found():
    return APIError("TASK_NOT_FOUND", "task is not available", status_code=404)


def _task_response(task, artifacts=(), *, request_id: str):
    error = None
    if task.error_code:
        error = ErrorResponse(
            code=task.error_code, message="task failed" if task.status is TaskStatus.FAILED else "task cancelled",
            request_id=request_id,
        )
    return AnalysisTaskResponse(
        task_id=task.task_id, query=task.query, dataset_ids=task.dataset_ids,
        status=task.status, max_rounds=task.max_rounds,
        created_at=task.created_at, updated_at=task.updated_at,
        error=error, metadata=task.metadata,
        artifacts=tuple(ArtifactResponse(
            artifact_id=artifact.artifact_id, artifact_type=artifact.artifact_type,
            name=artifact.name, mime_type=artifact.mime_type,
            description=artifact.description, created_at=artifact.created_at,
            metadata=artifact.metadata_json,
        ) for artifact in artifacts),
    )


@router.post("", response_model=TaskSubmissionResponse, status_code=status.HTTP_202_ACCEPTED)
def create_task(request: Request, payload: AnalysisTaskCreateRequest,
                principal: Principal = Depends(get_current_principal)):
    from ...worker.errors import TaskEnqueueError

    _, submission = _services(request)
    try:
        result = submission.submit(user_id=principal.user_id, request=payload)
    except EntityNotFoundError as exc:
        raise APIError("DATASET_NOT_FOUND", "dataset is not available", status_code=404) from exc
    except IdempotencyConflictError as exc:
        raise APIError("IDEMPOTENCY_CONFLICT", "idempotency key was reused", status_code=409) from exc
    except TaskEnqueueError as exc:
        raise APIError("TASK_ENQUEUE_FAILED", "task could not be queued", status_code=503) from exc
    except PersistenceError as exc:
        raise APIError("TASK_PERSISTENCE_FAILURE", "task service is unavailable", status_code=503) from exc
    return TaskSubmissionResponse(task_id=result.task.task_id, status=result.task.status,
                                  created=result.created, enqueued=result.enqueued)


@router.get("", response_model=TaskListResponse)
def list_tasks(request: Request, task_status: TaskStatus | None = Query(default=None, alias="status"),
               page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
               principal: Principal = Depends(get_current_principal)):
    persistence, _ = _services(request)
    params = PaginationParams(page=page, page_size=page_size)
    try:
        items, total = persistence.list_tasks_for_user(principal.user_id, task_status, params.offset, page_size)
    except PersistenceError as exc:
        raise APIError("TASK_PERSISTENCE_FAILURE", "task service is unavailable", status_code=503) from exc
    return TaskListResponse(items=[_task_response(item, request_id=request.state.request_id) for item in items], page=page,
                            page_size=page_size, total=total, has_next=params.offset + len(items) < total)


@router.get("/{task_id}", response_model=AnalysisTaskResponse)
def get_task(request: Request, task_id: UUID, principal: Principal = Depends(get_current_principal)):
    persistence, _ = _services(request)
    try:
        task = persistence.get_task_for_user(task_id, principal.user_id)
        if task is None:
            raise _not_found()
        artifacts = persistence.list_artifacts_for_user(task_id, principal.user_id)
        return _task_response(task, artifacts, request_id=request.state.request_id)
    except EntityNotFoundError as exc:
        raise _not_found() from exc
    except PersistenceError as exc:
        raise APIError("TASK_PERSISTENCE_FAILURE", "task service is unavailable", status_code=503) from exc


@router.get("/{task_id}/events", response_model=TaskEventListResponse)
def list_events(request: Request, task_id: UUID, page: int = Query(default=1, ge=1),
                page_size: int = Query(default=20, ge=1, le=100),
                principal: Principal = Depends(get_current_principal)):
    persistence, _ = _services(request)
    params = PaginationParams(page=page, page_size=page_size)
    try:
        events, total = persistence.list_events_for_user(task_id, principal.user_id, params.offset, page_size)
    except EntityNotFoundError as exc:
        raise _not_found() from exc
    except PersistenceError as exc:
        raise APIError("TASK_PERSISTENCE_FAILURE", "task service is unavailable", status_code=503) from exc
    return TaskEventListResponse(items=[TaskEventResponse.model_validate(event) for event in events],
                                 page=page, page_size=page_size, total=total,
                                 has_next=params.offset + len(events) < total)


@router.post("/{task_id}/cancel", response_model=AnalysisTaskResponse)
def cancel_task(request: Request, task_id: UUID, principal: Principal = Depends(get_current_principal)):
    _, submission = _services(request)
    try:
        return _task_response(submission.cancel_for_user(task_id, principal.user_id), request_id=request.state.request_id)
    except EntityNotFoundError as exc:
        raise _not_found() from exc
    except PersistenceError as exc:
        raise APIError("TASK_PERSISTENCE_FAILURE", "task service is unavailable", status_code=503) from exc


@router.post("/{task_id}/retry", response_model=TaskRetryResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_task(request: Request, task_id: UUID, principal: Principal = Depends(get_current_principal)):
    from ...services.persistence import TaskRetryConflictError
    from ...worker.errors import TaskEnqueueError

    _, submission = _services(request)
    try:
        result = submission.retry_for_user(task_id, principal.user_id)
    except EntityNotFoundError as exc:
        raise _not_found() from exc
    except TaskRetryConflictError as exc:
        raise APIError("TASK_NOT_RETRYABLE", "task is not failed", status_code=409) from exc
    except TaskEnqueueError as exc:
        raise APIError("TASK_ENQUEUE_FAILED", "task could not be queued", status_code=503) from exc
    except PersistenceError as exc:
        raise APIError("TASK_PERSISTENCE_FAILURE", "task service is unavailable", status_code=503) from exc
    return TaskRetryResponse(task_id=result.task.task_id, status=result.task.status, enqueued=result.enqueued)
