from __future__ import annotations

import re
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..config.settings import ConfigurationError, load_settings
from .application import APIApplication
from .auth import AuthenticationError
from .errors import APIError
from .routers import artifacts_router, datasets_router, tasks_router
from .schemas import ErrorResponse

_REQUEST_ID = re.compile(r"^[\x21-\x7e]{1,128}$")


def _request_id(request: Request) -> str:
    value = request.headers.get("X-Request-ID", "")
    return value if _REQUEST_ID.fullmatch(value) else str(uuid4())


async def request_id_middleware(request: Request, call_next):
    request.state.request_id = _request_id(request)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _error(request: Request, *, status: int, code: str, message: str, details=None):
    payload = ErrorResponse(
        code=code,
        message=message,
        details=details or {},
        request_id=getattr(request.state, "request_id", str(uuid4())),
    ).model_dump(mode="json")
    return JSONResponse(status_code=status, content=payload, headers={"X-Request-ID": payload["request_id"]})


def create_app(container: APIApplication | None = None) -> FastAPI:
    application = container or APIApplication.from_settings(load_settings())
    if application.settings.app_env == "production" and application.principal_provider is None:
        raise ConfigurationError("production requires an explicit authentication provider")
    if application.settings.app_env == "production" and (
        application.database is None or application.storage is None
    ):
        raise ConfigurationError("production requires configured database and storage")

    app = FastAPI(title="Data Analysis Agent API", version="1.0.0")
    app.state.api_application = application
    app.middleware("http")(request_id_middleware)
    app.include_router(datasets_router, prefix="/api")
    app.include_router(tasks_router, prefix="/api")
    app.include_router(artifacts_router, prefix="/api")

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = {
            "errors": [
                {"loc": list(error.get("loc", ())), "msg": error.get("msg", "invalid request"), "type": error.get("type", "")}
                for error in exc.errors()
            ]
        }
        return _error(request, status=422, code="REQUEST_VALIDATION_ERROR", message="request validation failed", details=details)

    @app.exception_handler(AuthenticationError)
    async def auth_handler(request: Request, exc: AuthenticationError):
        return _error(request, status=401, code="AUTHENTICATION_REQUIRED", message="authentication is required")

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return _error(request, status=exc.status_code, code=exc.code, message=exc.message, details=exc.details)

    @app.exception_handler(Exception)
    async def fallback_handler(request: Request, exc: Exception):
        return _error(request, status=500, code="INTERNAL_SERVER_ERROR", message="internal server error")

    return app
