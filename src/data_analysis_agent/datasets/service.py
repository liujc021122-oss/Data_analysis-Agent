from __future__ import annotations

from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Callable, Protocol
from uuid import UUID, uuid4

from ..domain.models import utc_now
from ..persistence.errors import TransactionError
from ..persistence.models import DatasetRecord, UserRecord
from ..persistence.unit_of_work import UnitOfWork
from .errors import DatasetErrorCode, DatasetPersistenceError, UploadValidationError
from .inspection import CsvInspector
from .models import DatasetUploadResult
from .storage import StorageBackend


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
    def __init__(self, *, storage: StorageBackend, inspector: CsvInspector,
                 metadata_store: DatasetMetadataStore, max_upload_size: int):
        self._storage = storage
        self._inspector = inspector
        self._metadata_store = metadata_store
        self._max_upload_size = max_upload_size

    def upload(self, stream: BinaryIO, *, original_filename: str,
               owner_id: UUID) -> DatasetUploadResult:
        safe_name = Path(original_filename.replace("\\", "/")).name
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
            stored = self._storage.put_stream(
                buffered,
                key=f"datasets/{dataset_id}.csv",
                max_bytes=self._max_upload_size,
            )
            record = DatasetRecord(
                dataset_id=dataset_id,
                user_id=owner_id,
                name=safe_name,
                source_uri=stored.uri,
                content_type="text/csv",
                size_bytes=size,
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
