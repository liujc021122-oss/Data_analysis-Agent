from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from ...persistence.unit_of_work import UnitOfWork
from ...services.auth import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
)
from ..auth import Principal, get_current_principal
from ..errors import APIError
from ..schemas import AuthLoginRequest, AuthRegisterRequest, AuthUserResponse


router = APIRouter(prefix="/auth", tags=["auth"])


def _service(request: Request):
    service = request.app.state.api_application.auth_service
    if service is None:
        raise APIError(
            "AUTH_SERVICE_UNAVAILABLE",
            "authentication service is unavailable",
            status_code=503,
        )
    return service


def _response(user) -> AuthUserResponse:
    return AuthUserResponse(
        user_id=user.user_id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/register", response_model=AuthUserResponse, status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: AuthRegisterRequest):
    try:
        user = _service(request).register(
            email=payload.email,
            password=payload.password,
            request_id=request.state.request_id,
        )
    except EmailAlreadyRegisteredError as exc:
        raise APIError(
            "EMAIL_ALREADY_REGISTERED",
            "email is already registered",
            status_code=409,
        ) from exc
    except InvalidEmailError as exc:
        raise APIError(
            "REQUEST_VALIDATION_ERROR",
            "request validation failed",
            status_code=422,
        ) from exc
    return _response(user)


@router.post("/login", response_model=AuthUserResponse)
def login(request: Request, response: Response, payload: AuthLoginRequest):
    try:
        result = _service(request).login(
            email=payload.email,
            password=payload.password,
            request_id=request.state.request_id,
        )
    except InvalidCredentialsError as exc:
        raise APIError(
            "INVALID_CREDENTIALS",
            "invalid credentials",
            status_code=401,
        ) from exc
    settings = request.app.state.api_application.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=result.token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return _response(result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    service = _service(request)
    settings = request.app.state.api_application.settings
    token = request.cookies.get(settings.session_cookie_name)
    service.logout(token=token or "", request_id=request.state.request_id)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=AuthUserResponse)
def me(request: Request, principal: Principal = Depends(get_current_principal)):
    application = request.app.state.api_application
    if application.database is None:
        raise APIError(
            "AUTH_SERVICE_UNAVAILABLE",
            "authentication service is unavailable",
            status_code=503,
        )
    with UnitOfWork(application.database.session_factory) as uow:
        record = uow.users.get(principal.user_id)
    if record is None or record.email_normalized is None:
        raise APIError("AUTHENTICATION_REQUIRED", "authentication is required", status_code=401)
    return AuthUserResponse(
        user_id=record.user_id,
        email=record.email_normalized,
        role=record.role,
        is_active=record.is_active,
        created_at=record.created_at,
    )
