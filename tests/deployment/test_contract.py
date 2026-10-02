from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_dockerfiles_use_runtime_secret_injection():
    backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    combined = backend + frontend

    assert "FROM python:3.12-slim" in backend
    assert "FROM node:" in frontend
    assert "npm ci" in frontend
    assert "VITE_API_BASE_URL" in frontend
    for secret_name in (
        "OPENAI_API_KEY",
        "MYSQL_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "STORAGE_SIGNING_SECRET",
    ):
        assert f"ARG {secret_name}" not in combined


def test_dockerignore_excludes_local_credentials_and_outputs():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert ".env.compose" in ignored
    assert ".venv" in ignored
    assert "deploy/certs" in ignored
    assert "outputs" in ignored


def test_compose_declares_the_approved_services():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {
        "frontend",
        "backend",
        "worker",
        "migrate",
        "mysql",
        "redis",
        "minio",
        "minio-init",
        "reverse-proxy",
    }
    assert compose["services"]["mysql"]["image"].startswith("mysql:8.0")
    assert compose["services"]["redis"]["command"] == "redis-server --appendonly yes"
    assert (
        compose["services"]["backend"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )
    assert (
        compose["services"]["worker"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )


def test_compose_uses_persistent_named_volumes_and_nonconflicting_ports():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert {"mysql_data", "redis_data", "minio_data"} <= set(compose["volumes"])
    assert "3307:3306" in compose["services"]["mysql"]["ports"]
    assert "8080:80" in compose["services"]["reverse-proxy"]["ports"]


def test_compose_does_not_place_secrets_in_build_args():
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY:" not in text.split("build:", 1)[-1]
    assert "MYSQL_PASSWORD" in text
    assert "STORAGE_SIGNING_SECRET" in text


def test_compose_gates_application_startup_and_uses_internal_dns():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["migrate"]["depends_on"]["mysql"]["condition"] == "service_healthy"
    for name in ("backend", "worker"):
        dependencies = services[name]["depends_on"]
        assert dependencies["mysql"]["condition"] == "service_healthy"
        assert dependencies["redis"]["condition"] == "service_healthy"
        assert dependencies["minio"]["condition"] == "service_healthy"
        assert dependencies["minio-init"]["condition"] == "service_completed_successfully"
        assert dependencies["migrate"]["condition"] == "service_completed_successfully"
        environment = "\n".join(services[name]["environment"])
        assert "DATABASE_URL=${DATABASE_URL}" in environment
        assert "REDIS_URL=${REDIS_URL}" in environment
        assert "STORAGE_ENDPOINT=${STORAGE_ENDPOINT}" in environment

    template = (ROOT / ".env.compose.example").read_text(encoding="utf-8")
    assert "@mysql:3306/data_analysis" in template
    assert "REDIS_URL=redis://redis:6379/0" in template
    assert "STORAGE_ENDPOINT=http://minio:9000" in template


def test_compose_defines_bounded_native_healthchecks_and_idempotent_storage_init():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    for name in ("mysql", "redis", "minio", "backend", "worker"):
        healthcheck = services[name]["healthcheck"]
        assert healthcheck["timeout"]
        assert healthcheck["retries"] > 0
        assert healthcheck["interval"]

    assert "mysqladmin ping" in " ".join(services["mysql"]["healthcheck"]["test"])
    assert "redis-cli ping" in " ".join(services["redis"]["healthcheck"]["test"])
    assert "/minio/health/ready" in " ".join(services["minio"]["healthcheck"]["test"])
    assert "urllib.request" in " ".join(services["backend"]["healthcheck"]["test"])
    assert "--healthcheck" in " ".join(services["worker"]["healthcheck"]["test"])
    assert "--ignore-existing" in " ".join(services["minio-init"]["command"])


def test_compose_template_contains_only_safe_placeholders_and_required_ignores():
    template = (ROOT / ".env.compose.example").read_text(encoding="utf-8")
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for key in (
        "APP_ENV",
        "OPENAI_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "STORAGE_SIGNING_SECRET",
        "LOG_LEVEL",
    ):
        assert f"{key}=" in template
    assert "change-me" in template
    assert ".env.compose" in ignored
    assert "deploy/certs/*.pem" in ignored
    assert "deploy/certs/*.key" in ignored
    assert "frontend/node_modules/" in ignored
    assert "frontend/dist/" in ignored
