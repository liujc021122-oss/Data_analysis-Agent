class WorkerError(Exception):
    """Base class for safe background-worker errors."""


class WorkerConfigurationError(WorkerError):
    pass


class TaskEnqueueError(WorkerError):
    pass


class RetryableTaskError(WorkerError):
    retryable = True


class PermanentTaskError(WorkerError):
    retryable = False


class TaskNotRunnableError(WorkerError):
    pass


__all__ = [
    "PermanentTaskError",
    "RetryableTaskError",
    "TaskEnqueueError",
    "TaskNotRunnableError",
    "WorkerConfigurationError",
    "WorkerError",
]
