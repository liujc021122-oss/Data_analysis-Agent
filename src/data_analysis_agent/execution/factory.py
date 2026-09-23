from __future__ import annotations

from ..config.settings import ConfigurationError, Settings
from .backend import CodeExecutionBackend
from .code_executor import LocalCodeExecutor
from .container_executor import ContainerCodeExecutor
from .runtime import ContainerRuntime


def build_execution_backend(
    settings: Settings,
    *,
    runtime: ContainerRuntime | None = None,
) -> CodeExecutionBackend:
    """Build the execution backend selected by typed application settings.

    Production is deliberately fail-closed: only the container backend is
    accepted and a missing image is a configuration error. Runtime availability
    is checked when a request executes, where it becomes a stable execution
    result; no local fallback is created here.
    """

    backend_name = getattr(settings, "execution_backend", None)
    if settings.app_env == "production" and backend_name != "container":
        raise ConfigurationError(
            "EXECUTION_BACKEND must be container in production"
        )

    if backend_name == "local":
        if settings.app_env == "production":
            raise ConfigurationError(
                "EXECUTION_BACKEND must be container in production"
            )
        return LocalCodeExecutor(settings.output_dir)

    if backend_name != "container":
        raise ConfigurationError(
            "EXECUTION_BACKEND must be local or container"
        )

    image = getattr(settings, "execution_image", None)
    if image is None or not str(image).strip():
        raise ConfigurationError(
            "EXECUTION_IMAGE is required for the container execution backend"
        )
    return ContainerCodeExecutor(str(image), runtime=runtime)


__all__ = ["build_execution_backend"]
