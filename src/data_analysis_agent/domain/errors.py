from enum import Enum

from .enums import TaskStatus


class DomainError(Exception):
    """Base class for safe domain-layer errors."""


class InvalidStatusTransitionError(DomainError):
    def __init__(self, current_status: TaskStatus, target_status: TaskStatus):
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            "Illegal task status transition: "
            f"{current_status.value} -> {target_status.value}"
        )


class PersistenceMappingError(DomainError):
    """A storage record cannot be converted into a domain object."""


class EvidenceErrorCode(str, Enum):
    EVIDENCE_TASK_MISMATCH = "EVIDENCE_TASK_MISMATCH"
    METRIC_CONFLICT = "METRIC_CONFLICT"
    METRIC_NOT_REPRODUCIBLE = "METRIC_NOT_REPRODUCIBLE"
    CHART_PATH_INVALID = "CHART_PATH_INVALID"
    CHART_NOT_FOUND = "CHART_NOT_FOUND"
    UNSUPPORTED_NUMERIC_CLAIM = "UNSUPPORTED_NUMERIC_CLAIM"
    EVIDENCE_REFERENCE_NOT_FOUND = "EVIDENCE_REFERENCE_NOT_FOUND"


class EvidenceError(DomainError):
    """Base error for safe, stable evidence validation failures."""

    def __init__(self, code: EvidenceErrorCode, message: str):
        self.code = code
        super().__init__(message)


class EvidenceReferenceError(EvidenceError):
    """A claim references an unknown metric or chart artifact."""
