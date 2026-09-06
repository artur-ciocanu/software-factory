"""Compatibility exports for canonical software-factory identities."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .validation import digest


def graph_identity(graph: Mapping[str, Any]) -> str:
    """Return the canonical SHA-256 identity for a closed graph value."""
    return digest(graph)
