from __future__ import annotations

from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from ..domain.models import utc_now
from ..persistence.errors import PersistenceError, TransactionError
from ..persistence.models import DatasetRecord, UserRecord
from ..persistence.unit_of_work import UnitOfWork
from ..services.authorization import AccessSubject
from ..storage import Storage, dataset_key, normalize_filename
from ..storage.errors import StorageError as CanonicalStorageError
from ..storage.errors import StorageErrorCode as CanonicalStorageErrorCode
from .errors import (
    DatasetAccessDeniedError,
    DatasetErrorCode,
    DatasetPersistenceError,
    StorageError,
    UploadValidationError,
)
from .inspection import CsvInspector
from .models import DatasetUploadResult


class DatasetMetadataStore(Protocol):
    def create(self, record: DatasetRecord) -> DatasetRecord:
        ...

    def get_for_user(self, dataset_id: UUID, user_id: UUID) -> DatasetRecord | None:
        ...


class UnitOfWorkDatasetStore:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]):
        self._uow_factory = uow_factory

    def create(self, record: DatasetRecord) -> DatasetRecord:
        try:
            with self._uow_factory() as uow:
                uow.users.ensure(UserRecord(user_id=record.user_id, created_at=record.created_at))
                created = uow.datasets.add(record)
                uow.commit()
                return created
        except TransactionError as exc:
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to persist dataset metadata",
            ) from exc

    def get_for_user(self, dataset_id: UUID, user_id: UUID) -> DatasetRecord | None:
        with self._uow_factory() as uow:
            return uow.datasets.get_for_user(dataset_id, user_id)

    def list_for_user(self, user_id: UUID, *, offset: int = 0, limit: int | None = None):
        with self._uow_factory() as uow:
            return uow.datasets.list_for_user(user_id, offset=offset, limit=limit), uow.datasets.count_for_user(user_id)

    def delete_for_user(self, dataset_id: UUID, user_id: UUID) -> bool:
        with self._uow_factory() as uow:
            deleted = uow.datasets.delete_for_user(dataset_id, user_id)
            uow.commit()
            return deleted


class DatasetCatalogService:
    def __init__(self, *, storage: Storage, uow_factory):
        self._storage = storage
        self._uow_factory = uow_factory

    def list_for_user(self, user_id: UUID, offset: int, limit: int):
        try:
            with self._uow_factory() as uow:
                return uow.datasets.list_for_user(user_id, offset=offset, limit=limit), uow.datasets.count_for_user(user_id)
        except (DatasetPersistenceError, PersistenceError, SQLAlchemyError) as exc:
            if isinstance(exc, DatasetPersistenceError):
                raise
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to read dataset metadata",
            ) from exc

    def list_for_subject(self, subject: AccessSubject, offset: int, limit: int):
        try:
            with self._uow_factory() as uow:
                return (
                    uow.datasets.list_for_subject(subject, offset=offset, limit=limit),
                    uow.datasets.count_for_subject(subject),
                )
        except (DatasetPersistenceError, PersistenceError, SQLAlchemyError) as exc:
            if isinstance(exc, DatasetPersistenceError):
                raise
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to read dataset metadata",
            ) from exc

    def get_for_user(self, user_id: UUID, dataset_id: UUID) -> DatasetRecord:
        try:
            with self._uow_factory() as uow:
                record = uow.datasets.get_for_user(dataset_id, user_id)
        except (DatasetPersistenceError, PersistenceError, SQLAlchemyError) as exc:
            if isinstance(exc, DatasetPersistenceError):
                raise
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to read dataset metadata",
            ) from exc
        if record is None:
            raise DatasetAccessDeniedError(DatasetErrorCode.DATASET_ACCESS_DENIED, "dataset is not available")
        return record

    def get_for_subject(self, subject: AccessSubject, dataset_id: UUID) -> DatasetRecord:
        try:
            with self._uow_factory() as uow:
                record = uow.datasets.get_for_subject(dataset_id, subject)
        except (DatasetPersistenceError, PersistenceError, SQLAlchemyError) as exc:
            if isinstance(exc, DatasetPersistenceError):
                raise
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to read dataset metadata",
            ) from exc
        if record is None:
            raise DatasetAccessDeniedError(
                DatasetErrorCode.DATASET_ACCESS_DENIED,
                "dataset is not available",
            )
        return record

    def delete_for_user(self, user_id: UUID, dataset_id: UUID) -> None:
        record = self.get_for_user(user_id, dataset_id)
        try:
            self._storage.delete(record.source_uri)
        except CanonicalStorageError as exc:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "unable to delete dataset object",
            ) from exc
        try:
            with self._uow_factory() as uow:
                if not uow.datasets.delete_for_user(dataset_id, user_id):
                    raise DatasetAccessDeniedError(
                        DatasetErrorCode.DATASET_ACCESS_DENIED,
                        "dataset is not available",
                    )
                uow.commit()
        except (DatasetAccessDeniedError, DatasetPersistenceError):
            raise
        except (TransactionError, PersistenceError, SQLAlchemyError) as exc:
            # The object was removed; retained metadata identifies the mismatch.
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to delete dataset metadata; reconciliation is required",
                details={"reconciliation_required": True},
            ) from exc

    def delete_for_subject(self, subject: AccessSubject, dataset_id: UUID) -> None:
        record = self.get_for_subject(subject, dataset_id)
        try:
            self._storage.delete(record.source_uri)
        except CanonicalStorageError as exc:
            raise StorageError(
                DatasetErrorCode.STORAGE_FAILURE,
                "unable to delete dataset object",
            ) from exc
        try:
            with self._uow_factory() as uow:
                if not uow.datasets.delete_for_subject(dataset_id, subject):
                    raise DatasetAccessDeniedError(
                        DatasetErrorCode.DATASET_ACCESS_DENIED,
                        "dataset is not available",
                    )
                uow.commit()
        except (DatasetAccessDeniedError, DatasetPersistenceError):
            raise
        except (TransactionError, PersistenceError, SQLAlchemyError) as exc:
            raise DatasetPersistenceError(
                DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                "unable to delete dataset metadata; reconciliation is required",
                details={"reconciliation_required": True},
            ) from exc


class InMemoryDatasetStore:
    """Offline quick_analysis adapter; it stores metadata only for one process."""

    def __init__(self):
        self._records: dict[UUID, DatasetRecord] = {}

    def create(self, record: DatasetRecord) -> DatasetRecord:
        self._records[record.dataset_id] = record
        return record

    def get_for_user(self, dataset_id: UUID, user_id: UUID) -> DatasetRecord | None:
        record = self._records.get(dataset_id)
        return record if record is not None and record.user_id == user_id else None


class DatasetUploadService:
    def __init__(self, *, storage: Storage, inspector: CsvInspector,
                 metadata_store: DatasetMetadataStore, max_upload_size: int):
        self._storage = storage
        self._inspector = inspector
        self._metadata_store = metadata_store
        self._max_upload_size = max_upload_size

    def upload(self, stream: BinaryIO, *, original_filename: str,
               owner_id: UUID) -> DatasetUploadResult:
        try:
            safe_name = normalize_filename(original_filename)
        except (TypeError, ValueError) as exc:
            raise UploadValidationError(
                DatasetErrorCode.INVALID_DATASET_REQUEST,
                "filename is invalid",
            ) from exc
        with SpooledTemporaryFile(max_size=self._max_upload_size, mode="w+b") as buffered:
            size = 0
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > self._max_upload_size:
                    raise UploadValidationError(
                        DatasetErrorCode.FILE_TOO_LARGE,
                        "upload exceeds the configured size limit",
                    )
                buffered.write(chunk)

            buffered.seek(0)
            profile = self._inspector.inspect(buffered, filename=safe_name)
            dataset_id = uuid4()
            buffered.seek(0)
            try:
                stored = self._storage.put(
                    buffered,
                    key=dataset_key(dataset_id),
                    content_type="text/csv",
                    max_bytes=self._max_upload_size,
                )
            except CanonicalStorageError as exc:
                if exc.code is CanonicalStorageErrorCode.FILE_TOO_LARGE:
                    raise UploadValidationError(
                        DatasetErrorCode.FILE_TOO_LARGE,
                        "upload exceeds the configured size limit",
                    ) from exc
                raise StorageError(
                    DatasetErrorCode.STORAGE_FAILURE,
                    "unable to store dataset",
                ) from exc
            record = DatasetRecord(
                dataset_id=dataset_id,
                user_id=owner_id,
                name=safe_name,
                source_uri=stored.uri,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
                checksum=stored.checksum,
                created_at=utc_now(),
                metadata_json={
                    "profile": profile.model_dump(mode="json"),
                    "original_filename": safe_name,
                },
            )
            try:
                self._metadata_store.create(record)
            except Exception as exc:
                try:
                    self._storage.delete(stored.uri)
                except Exception:
                    pass
                if isinstance(exc, DatasetPersistenceError):
                    raise
                raise DatasetPersistenceError(
                    DatasetErrorCode.DATASET_PERSISTENCE_FAILURE,
                    "unable to persist dataset metadata",
                ) from exc

        return DatasetUploadResult(
            dataset_id=dataset_id,
            original_filename=safe_name,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            profile=profile,
        )
