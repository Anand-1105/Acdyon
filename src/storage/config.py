"""Storage Configuration and Credential Management.

Defines typed configuration for database and Supabase persistence endpoints,
ensuring credentials remain server-side and are loaded safely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StorageConfig:
    """Configuration for PostgreSQL / Supabase persistence backend."""

    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> StorageConfig:
        """Create StorageConfig from standard environment variables."""
        return cls(
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY"),
            timeout_seconds=float(os.getenv("STORAGE_TIMEOUT_SECONDS", "10.0")),
        )

    @property
    def is_configured(self) -> bool:
        """Return True if both URL and Key are provided."""
        return bool(self.supabase_url and self.supabase_key)
