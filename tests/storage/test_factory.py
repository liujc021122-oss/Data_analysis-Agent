import builtins
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from data_analysis_agent.config.settings import ConfigurationError, Settings
from data_analysis_agent.storage.factory import build_storage
from data_analysis_agent.storage.local import LocalFileStorage
from data_analysis_agent.storage.s3 import S3Storage


def _settings(environment: str, **overrides):
    values = {
        "app_env": environment,
        "database_url": None,
        "redis_url": None,
        "storage_endpoint": "https://object-storage.example.invalid",
        "storage_bucket": "data-bucket",
        "storage_local_root": Path("outputs/datasets"),
        "openai_api_key": None,
        "openai_base_url": "https://api.deepseek.com",
        "openai_model": "deepseek-chat",
        "max_task_runtime": 900,
        "max_upload_size": 104857600,
        "output_dir": Path("outputs"),
        "log_level": "INFO",
        "storage_region": "test-region",
        "storage_access_key_id": "access-key",
        "storage_secret_access_key": "secret-key",
        "storage_signing_secret": None,
        "storage_url_expiry": 300,
        "storage_retention_days": 30,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_factory_returns_local_storage_without_importing_boto3(
    monkeypatch, tmp_path, environment
):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "boto3":
            raise AssertionError("local factory path imported boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    storage = build_storage(
        _settings(environment, storage_local_root=tmp_path / environment)
    )

    assert isinstance(storage, LocalFileStorage)
    assert storage._root == (tmp_path / environment).resolve()


def test_production_factory_constructs_s3_with_typed_storage_settings(monkeypatch):
    calls = []
    fake_boto3 = ModuleType("boto3")

    def client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return SimpleNamespace()

    fake_boto3.client = client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    storage = build_storage(_settings("production"))

    assert isinstance(storage, S3Storage)
    assert calls == [
        (
            "s3",
            {
                "endpoint_url": "https://object-storage.example.invalid",
                "region_name": "test-region",
                "aws_access_key_id": "access-key",
                "aws_secret_access_key": "secret-key",
            },
        )
    ]


@pytest.mark.parametrize(
    "missing_field",
    ["storage_endpoint", "storage_bucket"],
)
def test_production_factory_names_missing_object_storage_configuration(missing_field):
    with pytest.raises(ConfigurationError) as exc_info:
        build_storage(_settings("production", **{missing_field: None}))

    assert missing_field.upper() in str(exc_info.value)


def test_production_factory_allows_provider_default_credentials(monkeypatch):
    fake_boto3 = ModuleType("boto3")
    fake_boto3.client = lambda service_name, **kwargs: SimpleNamespace()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    storage = build_storage(
        _settings(
            "production",
            storage_access_key_id=None,
            storage_secret_access_key=None,
        )
    )

    assert isinstance(storage, S3Storage)


def test_production_factory_never_selects_local_backend():
    settings = _settings("production", storage_endpoint=None, storage_bucket=None)

    with pytest.raises(ConfigurationError):
        build_storage(settings)
