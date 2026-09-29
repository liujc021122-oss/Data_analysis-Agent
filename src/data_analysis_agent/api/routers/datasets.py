from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status, Request
from fastapi.responses import Response
from pydantic import ValidationError

from ...domain.enums import AuditAction
from ...datasets import DatasetAccessDeniedError
from ...datasets.errors import DatasetErrorCode, DatasetPersistenceError, StorageError, UploadValidationError
from ...datasets.models import DatasetProfile
from ...persistence.errors import PersistenceError
from ...services.authorization import AccessSubject
from ...storage.errors import StorageError as CanonicalStorageError
from ..auth import Principal, get_current_principal
from ..errors import APIError
from ..pagination import PaginationParams
from ..schemas import DatasetListResponse, DatasetResponse, DatasetUploadResponse

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _profile(record):
    try:
        return DatasetProfile.model_validate(record.metadata_json.get("profile", {}))
    except (ValidationError, AttributeError, TypeError) as exc:
        raise APIError("DATASET_PROFILE_INVALID", "dataset profile is unavailable", status_code=503) from exc


def _catalog(request: Request):
    service = request.app.state.api_application.dataset_catalog
    if service is None:
        raise APIError("DATASET_SERVICE_UNAVAILABLE", "dataset service is unavailable", status_code=503)
    return service


def _response(record) -> DatasetResponse:
    return DatasetResponse(dataset_id=record.dataset_id, name=record.name, content_type=record.content_type, size_bytes=record.size_bytes, checksum=record.checksum, created_at=record.created_at, profile=_profile(record))


def _subject(principal: Principal) -> AccessSubject:
    return AccessSubject(user_id=principal.user_id, role=principal.role)


@router.post("", response_model=DatasetUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_dataset(request: Request, file: UploadFile = File(...), principal: Principal = Depends(get_current_principal)):
    application = request.app.state.api_application
    if application.dataset_upload is None:
        raise APIError("DATASET_SERVICE_UNAVAILABLE", "dataset service is unavailable", status_code=503)
    try:
        result = application.dataset_upload.upload(
            file.file,
            original_filename=file.filename or "upload.csv",
            owner_id=principal.user_id,
            request_id=request.state.request_id,
            role=principal.role.value,
        )
    except UploadValidationError as exc:
        status_code = 413 if exc.code is DatasetErrorCode.FILE_TOO_LARGE else 422
        raise APIError(exc.code.value, "dataset upload is invalid", status_code=status_code) from exc
    except (StorageError, DatasetPersistenceError) as exc:
        raise APIError(exc.code.value, "dataset service is unavailable", status_code=503) from exc
    return DatasetUploadResponse(dataset_id=result.dataset_id, profile=result.profile)


@router.get("", response_model=DatasetListResponse)
def list_datasets(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_current_principal),
):
    params = PaginationParams(page=page, page_size=page_size)
    service = _catalog(request)
    try:
        records, total = service.list_for_subject(_subject(principal), params.offset, params.page_size)
    except (DatasetPersistenceError, PersistenceError) as exc:
        raise APIError("DATASET_PERSISTENCE_FAILURE", "dataset metadata is unavailable", status_code=503) from exc
    return DatasetListResponse(items=[_response(record) for record in records], page=page, page_size=page_size, total=total, has_next=params.offset + len(records) < total)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(request: Request, dataset_id: UUID, principal: Principal = Depends(get_current_principal)):
    try:
        return _response(_catalog(request).get_for_subject(_subject(principal), dataset_id))
    except DatasetAccessDeniedError as exc:
        request.app.state.api_application.record_audit(
            action=AuditAction.AUTHORIZATION_DENIED,
            user_id=principal.user_id,
            request_id=request.state.request_id,
            target_type="dataset",
            target_id=dataset_id,
            success=False,
            metadata={"reason_code": "DATASET_NOT_FOUND"},
        )
        raise APIError("DATASET_NOT_FOUND", "dataset is not available", status_code=404) from exc
    except (DatasetPersistenceError, PersistenceError) as exc:
        raise APIError("DATASET_PERSISTENCE_FAILURE", "dataset metadata is unavailable", status_code=503) from exc


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(request: Request, dataset_id: UUID, principal: Principal = Depends(get_current_principal)):
    try:
        _catalog(request).delete_for_subject(
            _subject(principal), dataset_id, request_id=request.state.request_id
        )
    except DatasetAccessDeniedError as exc:
        request.app.state.api_application.record_audit(
            action=AuditAction.AUTHORIZATION_DENIED,
            user_id=principal.user_id,
            request_id=request.state.request_id,
            target_type="dataset",
            target_id=dataset_id,
            success=False,
            metadata={"reason_code": "DATASET_NOT_FOUND"},
        )
        raise APIError("DATASET_NOT_FOUND", "dataset is not available", status_code=404) from exc
    except StorageError as exc:
        raise APIError(exc.code.value, "dataset service is unavailable", status_code=503) from exc
    except CanonicalStorageError as exc:
        raise APIError("STORAGE_FAILURE", "dataset service is unavailable", status_code=503) from exc
    except (DatasetPersistenceError, PersistenceError) as exc:
        raise APIError("DATASET_PERSISTENCE_FAILURE", "dataset metadata is unavailable", status_code=503) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
