import builtins
import hashlib
import sys
from io import BytesIO
from types import ModuleType

import pytest

from data_analysis_agent.storage.errors import StorageError, StorageErrorCode
from data_analysis_agent.storage.models import StorageObject
from data_analysis_agent.storage.s3 import MinIOStorage, S3Storage


class ProviderError(Exception):
    def __init__(self, code: str, status_code: int | None = None):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {
                "HTTPStatusCode": status_code or (404 if code == "404" else 500)
            },
        }
        super().__init__(code)


class FakeS3Client:
    def __init__(self):
        self.uploads = []
        self.objects = {}
        self.head_responses = {}
        self.get_responses = {}
        self.delete_calls = []
        self.presign_calls = []

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs):
        self.uploads.append(
            {
                "bucket": bucket,
                "key": key,
                "body": fileobj.read(),
                "extra_args": ExtraArgs,
            }
        )

    def get_object(self, *, Bucket, Key):
        response = self.get_responses.get((Bucket, Key))
        if isinstance(response, Exception):
            raise response
        return response or {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        response = self.head_responses.get((Bucket, Key))
        if isinstance(response, Exception):
            raise response
        return response

    def delete_object(self, *, Bucket, Key):
        self.delete_calls.append({"bucket": Bucket, "key": Key})

    def generate_presigned_url(self, ClientMethod, *, Params, ExpiresIn):
        self.presign_calls.append(
            {
                "method": ClientMethod,
                "params": Params,
                "expires_in": ExpiresIn,
            }
        )
        return "https://download.example.invalid/signed-object"


def _storage(client=None):
    return S3Storage(
        bucket="data-bucket",
        endpoint="https://object-storage.example.invalid",
        region="test-region",
        access_key_id="access-key",
        secret_access_key="secret-key",
        client=client or FakeS3Client(),
    )


def test_put_uploads_content_type_checksum_and_provider_neutral_metadata():
    client = FakeS3Client()
    storage = _storage(client)
    payload = b"name,value\nAda,42\n"
    key = "datasets/111/original.csv"
    checksum = f"sha256:{hashlib.sha256(payload).hexdigest()}"

    stored = storage.put(
        BytesIO(payload),
        key=key,
        content_type="text/csv; charset=utf-8",
        max_bytes=1024,
    )

    assert stored == StorageObject(
        uri=f"s3://data-bucket/{key}",
        key=key,
        size_bytes=len(payload),
        checksum=checksum,
        content_type="text/csv; charset=utf-8",
    )
    assert client.uploads == [
        {
            "bucket": "data-bucket",
            "key": key,
            "body": payload,
            "extra_args": {
                "ContentType": "text/csv; charset=utf-8",
                "Metadata": {"checksum": checksum},
            },
        }
    ]


def test_put_rejects_input_over_max_bytes_before_upload():
    client = FakeS3Client()

    with pytest.raises(StorageError) as exc_info:
        _storage(client).put(
            BytesIO(b"too large"),
            key="datasets/large.csv",
            content_type="text/csv",
            max_bytes=3,
        )

    assert exc_info.value.code is StorageErrorCode.FILE_TOO_LARGE
    assert client.uploads == []


def test_get_returns_provider_body_stream():
    client = FakeS3Client()
    client.get_responses[("data-bucket", "datasets/111/original.csv")] = {
        "Body": BytesIO(b"stored bytes")
    }
    storage = _storage(client)

    body = storage.get("s3://data-bucket/datasets/111/original.csv")

    assert body.read() == b"stored bytes"


def test_stat_maps_provider_metadata_to_storage_object():
    client = FakeS3Client()
    key = "tasks/222/reports/report.docx"
    client.head_responses[("data-bucket", key)] = {
        "ContentLength": 11,
        "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Metadata": {"checksum": "sha256:abc123"},
    }

    stored = _storage(client).stat(f"s3://data-bucket/{key}")

    assert stored == StorageObject(
        uri=f"s3://data-bucket/{key}",
        key=key,
        size_bytes=11,
        checksum="sha256:abc123",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@pytest.mark.parametrize("method", ["get", "stat"])
def test_get_and_stat_translate_provider_404_to_object_not_found(method):
    client = FakeS3Client()
    key = "datasets/missing.csv"
    client.get_responses[("data-bucket", key)] = ProviderError("NoSuchKey")
    client.head_responses[("data-bucket", key)] = ProviderError("404")
    storage = _storage(client)

    with pytest.raises(StorageError) as exc_info:
        getattr(storage, method)(f"s3://data-bucket/{key}")

    assert exc_info.value.code is StorageErrorCode.OBJECT_NOT_FOUND


def test_exists_returns_false_for_provider_404_and_true_for_existing_object():
    client = FakeS3Client()
    missing_key = "datasets/missing.csv"
    existing_key = "datasets/existing.csv"
    client.head_responses[("data-bucket", missing_key)] = ProviderError("404")
    client.head_responses[("data-bucket", existing_key)] = {
        "ContentLength": 1,
        "ContentType": "text/csv",
        "Metadata": {"checksum": "sha256:x"},
    }
    storage = _storage(client)

    assert not storage.exists(f"s3://data-bucket/{missing_key}")
    assert storage.exists(f"s3://data-bucket/{existing_key}")


@pytest.mark.parametrize("method", ["get", "stat", "exists", "delete"])
def test_non_404_provider_failures_are_stable_backend_errors(method):
    client = FakeS3Client()
    key = "datasets/failure.csv"
    failure = ProviderError("500", status_code=500)
    client.get_responses[("data-bucket", key)] = failure
    client.head_responses[("data-bucket", key)] = failure
    original_delete = client.delete_object

    def failing_delete(*, Bucket, Key):
        original_delete(Bucket=Bucket, Key=Key)
        raise failure

    client.delete_object = failing_delete
    storage = _storage(client)

    with pytest.raises(StorageError) as exc_info:
        getattr(storage, method)(f"s3://data-bucket/{key}")

    assert exc_info.value.code is StorageErrorCode.BACKEND_UNAVAILABLE
    message = str(exc_info.value)
    assert "object-storage.example.invalid" not in message
    assert "access-key" not in message
    assert "secret-key" not in message


def test_delete_calls_provider_delete_object():
    client = FakeS3Client()
    storage = _storage(client)

    storage.delete("s3://data-bucket/datasets/111/original.csv")

    assert client.delete_calls == [
        {"bucket": "data-bucket", "key": "datasets/111/original.csv"}
    ]


def test_create_download_url_forwards_expiry_and_object_reference():
    client = FakeS3Client()
    storage = _storage(client)

    url = storage.create_download_url(
        "s3://data-bucket/datasets/111/original.csv", expires_in=600
    )

    assert url == "https://download.example.invalid/signed-object"
    assert client.presign_calls == [
        {
            "method": "get_object",
            "params": {
                "Bucket": "data-bucket",
                "Key": "datasets/111/original.csv",
            },
            "expires_in": 600,
        }
    ]


def test_create_download_url_rejects_non_positive_expiry():
    storage = _storage(FakeS3Client())

    for expires_in in (0, -1):
        with pytest.raises(StorageError) as exc_info:
            storage.create_download_url(
                "s3://data-bucket/datasets/111/original.csv",
                expires_in=expires_in,
            )
        assert exc_info.value.code is StorageErrorCode.DOWNLOAD_URL_INVALID


def test_injected_client_does_not_import_boto3(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "boto3":
            raise AssertionError("injected-client construction imported boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert isinstance(_storage(FakeS3Client()), S3Storage)


def test_s3_client_construction_forwards_endpoint_and_credentials(monkeypatch):
    calls = []
    fake_boto3 = ModuleType("boto3")

    def client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return FakeS3Client()

    fake_boto3.client = client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    S3Storage(
        bucket="data-bucket",
        endpoint="https://minio.example.invalid",
        region="us-east-1",
        access_key_id="access-key",
        secret_access_key="secret-key",
    )

    assert calls == [
        (
            "s3",
            {
                "endpoint_url": "https://minio.example.invalid",
                "region_name": "us-east-1",
                "aws_access_key_id": "access-key",
                "aws_secret_access_key": "secret-key",
            },
        )
    ]


def test_minio_storage_forwards_endpoint_to_s3_client(monkeypatch):
    calls = []
    fake_boto3 = ModuleType("boto3")

    def client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return FakeS3Client()

    fake_boto3.client = client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    storage = MinIOStorage(
        bucket="minio-bucket",
        endpoint="http://minio.example.invalid:9000",
        access_key_id="access-key",
        secret_access_key="secret-key",
    )

    assert isinstance(storage, S3Storage)
    assert calls[0][1]["endpoint_url"] == "http://minio.example.invalid:9000"
