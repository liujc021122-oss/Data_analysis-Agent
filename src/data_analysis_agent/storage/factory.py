from __future__ import annotations

from typing import TYPE_CHECKING

from .local import LocalFileStorage

if TYPE_CHECKING:
    from . import Storage
    from ..config.settings import Settings


def build_storage(settings: Settings) -> Storage:
    """Build the storage adapter selected by the application environment."""

    if settings.app_env in {"development", "test"}:
        return LocalFileStorage(settings.storage_local_root)

    if settings.app_env != "production":
        from ..config.settings import ConfigurationError

        raise ConfigurationError("APP_ENV does not select a storage backend")

    required = {
        "STORAGE_ENDPOINT": settings.storage_endpoint,
        "STORAGE_BUCKET": settings.storage_bucket,
    }
    missing = [name for name, value in required.items() if not _has_value(value)]
    if missing:
        from ..config.settings import ConfigurationError

        raise ConfigurationError(
            "Missing required production object-storage configuration: "
            + ", ".join(missing)
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
