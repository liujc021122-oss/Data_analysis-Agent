FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements/constraints-py312.txt requirements/constraints-py312.txt
COPY pyproject.toml README.md ./
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini alembic.ini

RUN python -m pip install --constraint requirements/constraints-py312.txt ".[api,worker]"

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/outputs/datasets \
    && chown -R app:app /app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "data_analysis_agent.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
