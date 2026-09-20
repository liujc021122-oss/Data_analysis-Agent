from __future__ import annotations

from typing import BinaryIO
from uuid import UUID

from ..storage import Storage
from ..storage.errors import StorageError as CanonicalStorageError
from ..storage.errors import StorageErrorCode as CanonicalStorageErrorCode
from pydantic import ValidationError

from .errors import DatasetAccessDeniedError, DatasetErrorCode, DatasetPersistenceError
from .models import DatasetProfile
from .service import DatasetMetadataStore
class DatasetResolver:
    def __init__(self, *, storage: Storage, metadata_store: DatasetMetadataStore):
        self._storage = storage
        self._metadata_store = metadata_store

    def _record_for_user(self, dataset_id: UUID, owner_id: UUID):
        record = self._metadata_store.get_for_user(dataset_id, owner_id)
        if record is None:
            raise DatasetAccessDeniedError(
                DatasetErrorCode.DATASET_ACCESS_DENIED,
                "dataset is not available for this user",
            )
        return record

    def open_for_user(self, dataset_id: UUID, *, owner_id: UUID) -> BinaryIO:
        record = self._record_for_user(dataset_id, owner_id)
        try:
            if not self._storage.exists(record.source_uri):
                raise self._missing_object()
            stored = self._storage.stat(record.source_uri)
        except DatasetPersistenceError:
            raise
        except CanonicalStorageError as exc:
            raise self._map_storage_error(exc) from exc

        if (
            record.size_bytes != stored.size_bytes
            or (
                record.checksum is not None
                and record.checksum != stored.checksum
            )
        ):
            raise DatasetPersistenceError(
                DatasetErrorCode.STORAGE_METADATA_MISMATCH,
                "stored dataset does not match persisted metadata",
            )

        try:
            return self._storage.get(record.source_uri)
        except CanonicalStorageError as exc:
            raise self._map_storage_error(exc) from exc

    @staticmethod
    def _missing_object() -> DatasetPersistenceError:
        return DatasetPersistenceError(
            DatasetErrorCode.STORAGE_OBJECT_NOT_FOUND,
            "dataset storage object is not available",
        )

    @classmethod
    def _map_storage_error(cls, exc: CanonicalStorageError) -> DatasetPersistenceError:
        if exc.code is CanonicalStorageErrorCode.OBJECT_NOT_FOUND:
            return cls._missing_object()
        return DatasetPersistenceError(
            DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
            "unable to verify dataset storage object",
        )

    def profile_for_user(self, dataset_id: UUID, *, owner_id: UUID) -> DatasetProfile:
        record = self._record_for_user(dataset_id, owner_id)
        try:
            return DatasetProfile.model_validate(record.metadata_json["profile"])
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "persisted dataset profile is invalid",
            ) from exc
