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
