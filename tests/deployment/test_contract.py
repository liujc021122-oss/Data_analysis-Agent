from pathlib import Path


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
