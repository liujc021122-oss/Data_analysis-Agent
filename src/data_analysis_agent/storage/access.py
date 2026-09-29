from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import BinaryIO, TYPE_CHECKING
from urllib.parse import urlencode
from uuid import UUID

from ..services.authorization import AccessSubject
from .artifacts import ArtifactRepository
from .errors import StorageError

if TYPE_CHECKING:
    from ..persistence.models import ArtifactRecord
    from . import Storage


class FileAccessDeniedError(Exception):
    code = "FILE_ACCESS_DENIED"

    def __init__(self, message: str = "file is not available for this user") -> None:
        self.message = message
        super().__init__(message)


def artifact_content_url(artifact_id: UUID, download_url: str) -> str:
    """Map local signed tokens to the protected API content endpoint."""
    if download_url.startswith("local-download://"):
        query = urlencode({"download_url": download_url})
        return f"/api/artifacts/{artifact_id}/content?{query}"
    return download_url


class FileAccessService:
    def __init__(
        self,
        *,
        storage: Storage,
        artifact_repository: ArtifactRepository,
        content_signing_secret: bytes | None = None,
    ) -> None:
        self._storage = storage
        self._artifact_repository = artifact_repository
        self._content_signing_secret = content_signing_secret or secrets.token_bytes(32)

    def create_download_url(
        self,
        artifact_id: UUID,
        user_id: UUID,
        expires_in: int = 300,
    ) -> str:
        record = self._artifact_repository.get_for_user(artifact_id, user_id)
        try:
            stored = self._validated_record(record)
            return self._storage.create_download_url(
                record.file_path,
                expires_in=expires_in,
            )
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def create_download_url_for_subject(
        self,
        artifact_id: UUID,
        subject: AccessSubject,
        expires_in: int = 300,
    ) -> str:
        record = self._artifact_repository.get_for_subject(artifact_id, subject)
        try:
            self._validated_record(record)
            return self._storage.create_download_url(
                record.file_path,
                expires_in=expires_in,
            )
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def create_content_token_for_subject(
        self,
        artifact_id: UUID,
        subject: AccessSubject,
        expires_in: int = 300,
    ) -> str:
        record = self._artifact_repository.get_for_subject(artifact_id, subject)
        try:
            self._validated_record(record)
            if (
                not isinstance(expires_in, int)
                or isinstance(expires_in, bool)
                or expires_in <= 0
            ):
                raise FileAccessDeniedError()
            payload = json.dumps(
                {
                    "artifact_id": str(artifact_id),
                    "expires_at": int(time.time()) + expires_in,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            encoded_payload = _encode_token_part(payload)
            signature = hmac.new(
                self._content_signing_secret,
                payload,
                hashlib.sha256,
            ).digest()
            return f"app-download://{encoded_payload}.{_encode_token_part(signature)}"
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def open_download(
        self,
        artifact_id: UUID,
        user_id: UUID,
        download_url: str,
    ) -> tuple["ArtifactRecord", BinaryIO]:
        """Open a signed download token after rechecking its artifact owner."""
        record = self._artifact_repository.get_for_user(artifact_id, user_id)
        try:
            stored = self._validated_record(record)
            requested = self._storage.stat(download_url)
            if requested.uri != stored.uri or requested.key != stored.key:
                raise FileAccessDeniedError()
            return record, self._storage.get(download_url)
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def open_download_for_subject(
        self,
        artifact_id: UUID,
        subject: AccessSubject,
        download_url: str,
    ) -> tuple["ArtifactRecord", BinaryIO]:
        """Recheck subject scope, signature and stored metadata before reading."""
        record = self._artifact_repository.get_for_subject(artifact_id, subject)
        try:
            stored = self._validated_record(record)
            if download_url.startswith("app-download://"):
                if not self._verify_content_token(download_url, artifact_id):
                    raise FileAccessDeniedError()
                return record, self._storage.get(stored.uri)
            requested = self._storage.stat(download_url)
            if requested.uri != stored.uri or requested.key != stored.key:
                raise FileAccessDeniedError()
            return record, self._storage.get(download_url)
        except StorageError as exc:
            raise FileAccessDeniedError() from exc

    def _verify_content_token(self, token: str, artifact_id: UUID) -> bool:
        try:
            payload_part, signature_part = token.removeprefix(
                "app-download://"
            ).split(".", 1)
            payload = _decode_token_part(payload_part)
            signature = _decode_token_part(signature_part)
            expected = hmac.new(
                self._content_signing_secret,
                payload,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(signature, expected):
                return False
            claims = json.loads(payload.decode("utf-8"))
            return (
                claims.get("artifact_id") == str(artifact_id)
                and isinstance(claims.get("expires_at"), int)
                and claims["expires_at"] >= int(time.time())
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False

    def _validated_record(self, record: "ArtifactRecord | None"):
        if record is None or not record.file_path:
            raise FileAccessDeniedError()
        try:
            stored = self._storage.stat(record.file_path)
        except StorageError as exc:
            raise FileAccessDeniedError() from exc
        if (
            stored.size_bytes != record.size_bytes
            or (
                record.content_hash is not None
                and stored.checksum != record.content_hash
            )
        ):
            raise FileAccessDeniedError()
        return stored


def _encode_token_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_token_part(value: str) -> bytes:
    if not value or not isinstance(value, str):
        raise ValueError("invalid token part")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
