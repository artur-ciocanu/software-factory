"""Portable shared factory pipeline primitives for publisher compatibility."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .validation import POLICY, sha256


def canonical_json(value: Mapping[str, Any]) -> str:
    """Return deterministic JSON used as a graph/receipt identity input."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def graph_identity(graph: Mapping[str, Any]) -> str:
    """Derive a lowercase SHA-256 identity without filesystem dependencies."""
    return hashlib.sha256(canonical_json(graph).encode()).hexdigest()


def validate_candidate_sha(value: Any) -> str:
    """Compatibility helper for the prior pipeline's strict digest contract."""
    return sha256(value)


def make_receipt(*, task_id: str, project_id: str, graph_id: str, candidate_sha: str) -> dict[str, str]:
    return {"policy": POLICY, "task_id": task_id, "project_id": project_id,
            "graph_id": graph_id, "candidate_sha": validate_candidate_sha(candidate_sha)}
