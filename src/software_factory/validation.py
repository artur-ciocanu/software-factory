"""Closed-schema and evidence validation shared by all plugin handlers."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
MARKER = re.compile(r"\A<!-- SOFTWARE-FACTORY-PLAN\n(.*?)\n-->\Z", re.DOTALL)
RECEIPT = re.compile(r"\A<!-- SOFTWARE-FACTORY-RECEIPT\n(.*?)\n-->\Z", re.DOTALL)
POLICY = "software-factory/v1"


def require_object(value: Any, allowed: set[str], required: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError("value does not satisfy its closed object schema")
    return value


def sha256(value: Any) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest")
    return value


def decode_marker(body: Any, pattern: re.Pattern[str]) -> dict[str, Any]:
    if not isinstance(body, str) or (match := pattern.fullmatch(body)) is None:
        raise ValueError("required raw factory marker is absent")
    try:
        decoded = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError("factory marker JSON is malformed") from exc
    if not isinstance(decoded, dict):
        raise ValueError("factory marker must contain an object")
    return decoded


def validate_plan(plan: Any, *, task_id: str, project_id: str) -> dict[str, Any]:
    obj = require_object(plan, {"policy", "task_id", "project_id", "inventory", "graph_id"},
                         {"policy", "task_id", "project_id", "inventory", "graph_id"})
    if obj["policy"] != POLICY or obj["task_id"] != task_id or obj["project_id"] != project_id:
        raise ValueError("plan policy or active task identity does not match")
    if not isinstance(obj["graph_id"], str) or not obj["graph_id"]:
        raise ValueError("graph_id must be a nonempty string")
    inventory = obj["inventory"]
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("inventory must be a nonempty list")
    seen: set[str] = set()
    for entry in inventory:
        item = require_object(entry, {"attachment_id", "filename", "sha256", "size"},
                              {"attachment_id", "filename", "sha256", "size"})
        if not isinstance(item["attachment_id"], int) or item["attachment_id"] < 1:
            raise ValueError("attachment_id must be positive")
        if not isinstance(item["filename"], str) or not item["filename"] or "/" in item["filename"]:
            raise ValueError("inventory filename must be a basename")
        if item["filename"] in seen or not isinstance(item["size"], int) or item["size"] < 0:
            raise ValueError("inventory is malformed")
        sha256(item["sha256"])
        seen.add(item["filename"])
    return dict(obj)


def verify_attachment(entry: Mapping[str, Any], metadata: Any, payload: bytes) -> None:
    item = require_object(metadata, {"attachment_id", "filename", "sha256", "size"},
                          {"attachment_id", "filename", "sha256", "size"})
    if any(item[key] != entry[key] for key in ("attachment_id", "filename", "sha256", "size")):
        raise ValueError("attachment metadata differs from validated inventory")
    if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise ValueError("attachment bytes fail inventory verification")


def receipt_body(receipt: Mapping[str, Any]) -> str:
    return "<!-- SOFTWARE-FACTORY-RECEIPT\n" + json.dumps(receipt, sort_keys=True) + "\n-->"
