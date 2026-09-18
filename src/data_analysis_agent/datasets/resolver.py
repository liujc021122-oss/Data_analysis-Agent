from __future__ import annotations

from typing import BinaryIO
from uuid import UUID

from pydantic import ValidationError

from .errors import DatasetAccessDeniedError, DatasetErrorCode, DatasetPersistenceError
from .models import DatasetProfile
from .service import DatasetMetadataStore
from .storage import StorageBackend


class DatasetResolver:
    def __init__(self, *, storage: StorageBackend, metadata_store: DatasetMetadataStore):
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
        return self._storage.open(record.source_uri)

    def profile_for_user(self, dataset_id: UUID, *, owner_id: UUID) -> DatasetProfile:
        record = self._record_for_user(dataset_id, owner_id)
        try:
            return DatasetProfile.model_validate(record.metadata_json["profile"])
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "persisted dataset profile is invalid",
            ) from exc
