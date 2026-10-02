from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

from .errors import StorageError, StorageErrorCode
from .keys import validate_key
from .models import StorageObject


class S3Storage:
    """S3-compatible implementation of the provider-neutral storage port."""

    _CHUNK_SIZE = 64 * 1024
    _DEFAULT_CONTENT_TYPE = "application/octet-stream"
    _SPOOL_MAX_SIZE = 8 * 1024 * 1024

    def __init__(
        self,
        bucket: str,
        endpoint: str,
        *,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._bucket = bucket
        self._endpoint = endpoint
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client = client if client is not None else self._create_client()

    def healthcheck(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception as exc:
            raise self._backend_error("check object storage health") from exc

    def put(
        self,
        stream: BinaryIO,
        *,
        key: str,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StorageObject:
        if (
            max_bytes is not None
            and (
                not isinstance(max_bytes, int)
                or isinstance(max_bytes, bool)
                or max_bytes <= 0
            )
        ):
            raise StorageError(
                StorageErrorCode.FILE_TOO_LARGE,
                "max_bytes must be positive",
            )

        key = validate_key(key)
        size = 0
        digest = hashlib.sha256()

        try:
            with tempfile.SpooledTemporaryFile(
                max_size=self._SPOOL_MAX_SIZE,
                mode="w+b",
            ) as buffered:
                while chunk := stream.read(self._CHUNK_SIZE):
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError("storage input must be binary")
                    chunk = bytes(chunk)
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise StorageError(
                            StorageErrorCode.FILE_TOO_LARGE,
                            "stream exceeds max_bytes",
                        )
                    buffered.write(chunk)
                    digest.update(chunk)

                checksum = f"sha256:{digest.hexdigest()}"
                buffered.seek(0)
                self._client.upload_fileobj(
                    buffered,
                    self._bucket,
                    key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "Metadata": {"checksum": checksum},
                    },
                )
        except StorageError:
            raise
        except Exception as exc:
            raise self._backend_error("upload stored object") from exc

        return StorageObject(
            uri=self._uri_for_key(key),
            key=key,
            size_bytes=size,
            checksum=checksum,
            content_type=content_type,
        )

    def get(self, uri: str) -> BinaryIO:
        key = self._key_for_uri(uri)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            return body
        except Exception as exc:
            if self._is_not_found(exc):
                raise StorageError(
                    StorageErrorCode.OBJECT_NOT_FOUND,
                    "stored object was not found",
                ) from exc
            raise self._backend_error("read stored object") from exc

    def stat(self, uri: str) -> StorageObject:
        key = self._key_for_uri(uri)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
            size = response["ContentLength"]
            content_type = response.get("ContentType") or self._DEFAULT_CONTENT_TYPE
            checksum = self._checksum_from_response(response)
        except Exception as exc:
            if self._is_not_found(exc):
                raise StorageError(
                    StorageErrorCode.OBJECT_NOT_FOUND,
                    "stored object was not found",
                ) from exc
            raise self._backend_error("inspect stored object") from exc

        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(content_type, str)
            or not isinstance(checksum, str)
        ):
            raise self._backend_error("inspect stored object")

        return StorageObject(
            uri=self._uri_for_key(key),
            key=key,
            size_bytes=size,
            checksum=checksum,
            content_type=content_type,
        )

    def exists(self, uri: str) -> bool:
        key = self._key_for_uri(uri)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            raise self._backend_error("inspect stored object") from exc

    def delete(self, uri: str) -> None:
        key = self._key_for_uri(uri)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if self._is_not_found(exc):
                return
            raise self._backend_error("delete stored object") from exc

    def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
        if (
            not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise StorageError(
                StorageErrorCode.DOWNLOAD_URL_INVALID,
                "download URL expiry must be positive",
            )

        key = self._key_for_uri(uri)
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise self._backend_error("create download URL") from exc

    def _create_client(self) -> Any:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
        )

    def _key_for_uri(self, uri: str) -> str:
        if not isinstance(uri, str) or "?" in uri or "#" in uri:
            raise self._invalid_uri()

        try:
            parsed = urlsplit(uri)
        except ValueError as exc:
            raise self._invalid_uri() from exc

        if parsed.scheme != "s3" or parsed.netloc != self._bucket or not parsed.path:
            raise self._invalid_uri()

        raw_key = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        try:
            key = unquote(raw_key)
            validate_key(key)
        except StorageError:
            raise
        except Exception as exc:
            raise self._invalid_uri() from exc
        return key

    def _uri_for_key(self, key: str) -> str:
        return f"s3://{self._bucket}/{key}"

    @staticmethod
    def _checksum_from_response(response: Mapping[str, Any]) -> str:
        metadata = response.get("Metadata") or {}
        if isinstance(metadata, Mapping):
            checksum = metadata.get("checksum") or metadata.get("sha256")
            if isinstance(checksum, str):
                if checksum.startswith("sha256:"):
                    return checksum
                return f"sha256:{checksum}"

        checksum = response.get("ChecksumSHA256")
        return checksum if isinstance(checksum, str) else ""

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        response = getattr(error, "response", None)
        if not isinstance(response, Mapping):
            return False

        error_payload = response.get("Error")
        error_code = (
            error_payload.get("Code")
            if isinstance(error_payload, Mapping)
            else None
        )
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return str(error_code) in {
            "404",
            "NoSuchKey",
            "NoSuchObject",
            "NoSuchBucket",
            "NotFound",
        } or str(status_code) == "404"

    @staticmethod
    def _invalid_uri() -> StorageError:
        return StorageError(
            StorageErrorCode.INVALID_URI,
            "storage URI is invalid",
        )

    @staticmethod
    def _backend_error(operation: str) -> StorageError:
        return StorageError(
            StorageErrorCode.BACKEND_UNAVAILABLE,
            f"unable to {operation}",
        )


class MinIOStorage(S3Storage):
    """MinIO-compatible storage using the same S3 object contract."""

    pass
