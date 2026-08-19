"""Source Adapter Registry.

Maintains a controlled registry of permitted source adapters mapped by source name.
Prevents dynamic arbitrary URL fetching and provides extension points for new sources.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.adapters.base import BaseSourceAdapter
from src.adapters.wwr.adapter import WWRSourceAdapter


class SourceAdapterRegistry:
    """Registry mapping source names to their configured BaseSourceAdapter instances."""

    def __init__(self) -> None:
        self._adapters: Dict[str, BaseSourceAdapter] = {}

    def register(self, adapter: BaseSourceAdapter) -> None:
        """Register a source adapter under its source_name."""
        self._adapters[adapter.source_name] = adapter

    def get(self, source_name: str) -> Optional[BaseSourceAdapter]:
        """Retrieve a registered adapter by source_name (case-insensitive)."""
        return self._adapters.get(source_name.strip().lower())

    def list_sources(self) -> List[str]:
        """Return a sorted list of registered source names."""
        return sorted(self._adapters.keys())

    @classmethod
    def default_registry(cls) -> SourceAdapterRegistry:
        """Construct the default registry pre-configured with standard adapters."""
        registry = cls()
        registry.register(WWRSourceAdapter())
        return registry
