import re
from typing import Iterable


_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[^\s,;]+")
_CREDENTIAL_URL_RE = re.compile(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|token)=)[^&#\s]+"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|token)\b\s*[:=]\s*)(['\"]?)[^,\s'\";)}]+"
)


def sanitize_text(text: object, *, secrets: Iterable[object] = ()) -> str:
    """Remove configured and conventionally encoded credentials from text."""
    value = str(text)
    for secret in sorted(
        (str(secret) for secret in secrets if secret is not None and str(secret)),
        key=len,
        reverse=True,
    ):
        value = value.replace(secret, "[REDACTED]")
    value = _BEARER_RE.sub(r"\1[REDACTED]", value)
    value = _CREDENTIAL_URL_RE.sub(r"\1[REDACTED]@", value)
    value = _SENSITIVE_QUERY_RE.sub(r"\1[REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", value)


def sanitize_exception(
    exception: BaseException,
    *,
    secrets: Iterable[object] = (),
    include_message: bool = True,
) -> str:
    """Return exception diagnostics without exposing raw provider payloads."""
    exception_name = type(exception).__name__
    if not include_message:
        return exception_name
    message = sanitize_text(exception, secrets=secrets)
    return f"{exception_name}: {message}" if message else exception_name
