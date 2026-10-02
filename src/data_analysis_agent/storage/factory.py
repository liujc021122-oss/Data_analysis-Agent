from __future__ import annotations

from typing import TYPE_CHECKING

from .local import LocalFileStorage

if TYPE_CHECKING:
    from . import Storage
    from ..config.settings import Settings


def build_storage(settings: Settings) -> Storage:
    """Build the explicitly selected storage adapter."""

    from ..config.settings import ConfigurationError

    signing_secret = (
        settings.storage_signing_secret.encode("utf-8")
        if settings.storage_signing_secret
        else None
    )
    if settings.storage_backend == "local":
        return LocalFileStorage(
            settings.storage_local_root,
            signing_secret=signing_secret,
        )
    if settings.storage_backend != "s3":
        raise ConfigurationError("STORAGE_BACKEND must be one of local or s3")
    if not _has_value(settings.storage_endpoint) or not _has_value(
        settings.storage_bucket
    ):
        raise ConfigurationError(
            "STORAGE_ENDPOINT and STORAGE_BUCKET are required for the s3 backend"
        )

    from .s3 import S3Storage

    return S3Storage(
        bucket=settings.storage_bucket,
        endpoint=settings.storage_endpoint,
        region=settings.storage_region,
        access_key_id=settings.storage_access_key_id,
        secret_access_key=settings.storage_secret_access_key,
    )


def _has_value(value: object) -> bool:
    return value is not None and bool(str(value).strip())
