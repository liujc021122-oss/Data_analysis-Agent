from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StorageObject:
    """Provider-neutral metadata for an object managed by storage."""

    uri: str
    size_bytes: int
    checksum: str
    content_type: str = "text/csv"
    key: str = ""
