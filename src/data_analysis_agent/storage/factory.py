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
        "STORAGE_ENDPOINT": getattr(settings, "storage_endpoint", None),
        "STORAGE_BUCKET": getattr(settings, "storage_bucket", None),
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
        bucket=getattr(settings, "storage_bucket", None),
        endpoint=getattr(settings, "storage_endpoint", None),
        region=getattr(settings, "storage_region", None),
        access_key_id=getattr(settings, "storage_access_key_id", None),
        secret_access_key=getattr(settings, "storage_secret_access_key", None),
    )


def _has_value(value: object) -> bool:
    return value is not None and bool(str(value).strip())
