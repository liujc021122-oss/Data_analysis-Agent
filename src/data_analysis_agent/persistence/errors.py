class PersistenceError(Exception):
    """Base class for database persistence failures."""


class DatabaseConfigurationError(PersistenceError, ValueError):
    """The configured database URL cannot be used."""


class EntityNotFoundError(PersistenceError):
    """A required persisted entity does not exist."""


class IdempotencyConflictError(PersistenceError):
    """An idempotency key was reused for a different request."""


class TransactionError(PersistenceError):
    """A database transaction failed."""
