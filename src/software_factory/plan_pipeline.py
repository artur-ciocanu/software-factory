"""Compatibility exports for canonical software-factory identities."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .validation import digest
from .validation import graph_identity as _graph_identity


def graph_identity(graph: Mapping[str, Any]) -> str:
    """Return a canonical graph identity, neutralizing handoff self-claims."""
    plan = graph.get("verified_plan")
    if isinstance(plan, dict) and isinstance(plan.get("supersession"), dict):
        return _graph_identity(graph)
    return digest(graph)
