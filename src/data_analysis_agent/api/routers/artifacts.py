from uuid import UUID
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ...domain.enums import AuditAction
from ...persistence.errors import PersistenceError
from ...persistence.models import ArtifactRecord
from ...persistence.unit_of_work import UnitOfWork
from ...services.authorization import AccessSubject
from ...storage import FileAccessDeniedError, artifact_content_url
from ..application import APIApplication
from ..auth import Principal, get_current_principal
from ..errors import APIError
from ..schemas import ArtifactDownloadResponse, ArtifactResponse

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _not_found() -> APIError:
    return APIError(
        "ARTIFACT_NOT_FOUND", "artifact is not available", status_code=404
    )


def _services(request: Request) -> APIApplication:
    application = request.app.state.api_application
    if application.database is None or application.file_access is None:
        raise APIError(
            "ARTIFACT_SERVICE_UNAVAILABLE",
            "artifact service is unavailable",
            status_code=503,
        )
    return application


def _record_denied(request: Request, subject: AccessSubject, artifact_id: UUID, reason_code: str = "ARTIFACT_NOT_FOUND") -> None:
    request.app.state.api_application.record_audit(
        action=AuditAction.ARTIFACT_DOWNLOAD_DENIED,
        user_id=subject.user_id,
        request_id=request.state.request_id,
        target_type="artifact",
        target_id=artifact_id,
        success=False,
        metadata={"reason_code": reason_code, "role": subject.role.value},
    )


def _record_and_url(
    request: Request, artifact_id: UUID, subject: AccessSubject
) -> tuple[ArtifactRecord, str, int]:
    application = _services(request)
    try:
        with UnitOfWork(application.database.session_factory) as uow:
            record = uow.artifacts.get_for_subject(artifact_id, subject)
        if record is None:
            _record_denied(request, subject, artifact_id)
            raise _not_found()
        url = application.file_access.create_download_url_for_subject(
            artifact_id,
            subject,
            expires_in=application.settings.storage_url_expiry,
        )
    except FileAccessDeniedError as exc:
        _record_denied(request, subject, artifact_id)
        raise _not_found() from exc
    except PersistenceError as exc:
        raise APIError(
            "ARTIFACT_PERSISTENCE_FAILURE",
            "artifact service is unavailable",
            status_code=503,
        ) from exc
    return record, url, application.settings.storage_url_expiry


def _content_url(
    request: Request,
    artifact_id: UUID,
    download_url: str,
    subject: AccessSubject,
    expires_in: int,
) -> str:
    if download_url.startswith("local-download://"):
        return artifact_content_url(artifact_id, download_url)
    application = _services(request)
    try:
        token = application.file_access.create_content_token_for_subject(
            artifact_id,
            subject,
            expires_in=expires_in,
        )
    except FileAccessDeniedError as exc:
        _record_denied(request, subject, artifact_id)
        raise _not_found() from exc
    query = urlencode({"download_url": token})
    return f"/api/artifacts/{artifact_id}/content?{query}"


@router.get("/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(
    request: Request,
    artifact_id: UUID,
    principal: Principal = Depends(get_current_principal),
):
    subject = AccessSubject(user_id=principal.user_id, role=principal.role)
    record, url, expiry = _record_and_url(
        request,
        artifact_id,
        subject,
    )
    return ArtifactResponse(
        artifact_id=record.artifact_id,
        artifact_type=record.artifact_type,
        name=record.name,
        download_url=url,
        content_url=_content_url(request, record.artifact_id, url, subject, expiry),
        format=record.format,
        mime_type=record.mime_type,
        description=record.description,
        created_at=record.created_at,
        metadata=record.metadata_json,
    )


@router.get("/{artifact_id}/download", response_model=ArtifactDownloadResponse)
def download_artifact(
    request: Request,
    artifact_id: UUID,
    principal: Principal = Depends(get_current_principal),
):
    subject = AccessSubject(user_id=principal.user_id, role=principal.role)
    _record, url, expires_in = _record_and_url(
        request,
        artifact_id,
        subject,
    )
    return ArtifactDownloadResponse(
        artifact_id=artifact_id,
        download_url=url,
        content_url=_content_url(request, artifact_id, url, subject, expires_in),
        expires_in=expires_in,
    )


@router.get("/{artifact_id}/content")
def download_artifact_content(
    request: Request,
    artifact_id: UUID,
    download_url: str | None = Query(default=None),
    token: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
):
    if download_url is None and token is not None:
        download_url = token
    if not download_url or (token is not None and download_url != token):
        _record_denied(
            request,
            AccessSubject(user_id=principal.user_id, role=principal.role),
            artifact_id,
        )
        raise _not_found()
    application = _services(request)
    subject = AccessSubject(user_id=principal.user_id, role=principal.role)
    try:
        record, stream = application.file_access.open_download_for_subject(
            artifact_id,
            subject,
            download_url,
        )
    except FileAccessDeniedError as exc:
        _record_denied(request, subject, artifact_id)
        raise _not_found() from exc
    request.app.state.api_application.record_audit(
        action=AuditAction.ARTIFACT_DOWNLOAD_SUCCEEDED,
        user_id=subject.user_id,
        request_id=request.state.request_id,
        target_type="artifact",
        target_id=artifact_id,
        success=True,
        metadata={
            "resource_type": "artifact",
            "role": subject.role.value,
        },
    )
    safe_name = quote(record.name, safe="")
    return StreamingResponse(
        stream,
        media_type=record.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"
        },
    )
