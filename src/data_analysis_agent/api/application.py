from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config.settings import Settings, load_settings
from ..storage import Storage
from .auth import PrincipalProvider


@dataclass
class APIApplication:
    """Dependencies shared by API routes for one application instance."""

    settings: Settings = field(default_factory=load_settings)
    database: Any = None
    storage: Storage | None = None
    dataset_upload: Any = None
    dataset_catalog: Any = None
    task_persistence: Any = None
    task_submission: Any = None
    file_access: Any = None
    principal_provider: PrincipalProvider | None = None

