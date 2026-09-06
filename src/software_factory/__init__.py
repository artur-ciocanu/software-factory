"""Runtime-correct, fail-closed Hermes Kanban software-factory plugin."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .validation import (
    HANDOFF_FILENAMES,
    RECEIPT_FILENAME,
    ROOT_MARKER,
    UNIT_MARKER,
    ContractError,
    VerifiedHandoff,
    canonical_json,
    digest,
    parse_candidate_receipt,
    validate_evidence,
)

TOOLSET = "software-factory"
ROLES = {
    "quentin": {"software_factory_preflight_verified_plan", "software_factory_read_receipt"},
    "sheila": {"software_factory_materialize_verified_plan", "software_factory_read_receipt"},
    "coddy": {"software_factory_publish_candidate_receipt", "software_factory_read_receipt"},
    "tammy": {"software_factory_validate_candidate_receipt", "software_factory_read_receipt"},
}


def _schema(name: str) -> dict[str, Any]:
    return {"name": name, "description": "Fail-closed software-factory operation.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}


SCHEMAS = {name: _schema(name) for names in ROLES.values() for name in names}


def _profile(ctx: Any | None = None) -> str:
    value = getattr(ctx, "profile_name", None) if ctx is not None else None
    if not isinstance(value, str) or not value.strip():
        value = os.environ.get("HERMES_PROFILE_NAME") or os.environ.get("HERMES_PROFILE")
    return value.strip().lower() if isinstance(value, str) else ""


def _require_role(ctx: Any, role: str) -> None:
    if _profile(ctx) != role:
        raise ContractError(f"software-factory handler requires {role} runtime profile")


def _active_task_id() -> str:
    task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if not task_id:
        raise ContractError("software-factory tools require an active Kanban task")
    return task_id


def _decode(value: Any, operation: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ContractError(f"{operation} did not return a JSON string")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{operation} returned malformed JSON") from exc
    if not isinstance(decoded, dict) or decoded.get("ok") is False:
        raise ContractError(f"{operation} failed or returned malformed data")
    return decoded


def _dispatch(ctx: Any, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    if name not in {"kanban_show", "kanban_attachments", "kanban_create", "kanban_attach"}:
        raise ContractError("unsupported native operation")
    return _decode(ctx.dispatch_tool(name, dict(args)), name)


def _cli_task(task_id: str) -> dict[str, Any]:
    """Read the public CLI task representation used only for project identity."""
    try:
        result = subprocess.run(
            ["hermes", "kanban", "show", task_id, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("fixed Hermes CLI project identity read failed") from exc
    if result.returncode != 0:
        raise ContractError("fixed Hermes CLI project identity read failed")
    try:
        task = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("fixed Hermes CLI project identity read returned malformed JSON") from exc
    if not isinstance(task, dict) or task.get("id") != task_id:
        raise ContractError("fixed Hermes CLI task identity is malformed")
    return task


def _show(ctx: Any, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _dispatch(ctx, "kanban_show", {"task_id": task_id})
    native_task = result.get("task")
    if not isinstance(native_task, dict) or native_task.get("id") != task_id or not isinstance(native_task.get("body"), str):
        raise ContractError("kanban_show task envelope is malformed")
    cli_task = _cli_task(task_id)
    for field in ("id", "body", "assignee", "workspace_kind", "workspace_path", "status"):
        if field in native_task and field in cli_task and native_task[field] != cli_task[field]:
            raise ContractError(f"native and CLI task {field} differ")
    project_id = cli_task.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ContractError("active task has no project_id")
    if not isinstance(result.get("parents"), list):
        raise ContractError("kanban_show parents are malformed")
    task = {**native_task, "project_id": project_id}
    return task, result


def _attachment_root() -> Path:
    override = os.environ.get("HERMES_KANBAN_ATTACHMENTS_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    return (Path(home).expanduser() if home else Path.home() / ".hermes").resolve() / "kanban" / "attachments"


def _read_attachment(task_id: str, metadata: Mapping[str, Any]) -> bytes:
    filename, stored_path, size = metadata.get("filename"), metadata.get("stored_path"), metadata.get("size")
    if not isinstance(metadata.get("id"), int) or not isinstance(filename, str) or Path(filename).name != filename or not isinstance(stored_path, str) or type(size) is not int or size < 0:
        raise ContractError("attachment metadata is malformed")
    root = _attachment_root()
    path = Path(stored_path).resolve(strict=True)
    try:
        path.relative_to(root / task_id)
    except ValueError as exc:
        raise ContractError("attachment path is outside the active task attachment root") from exc
    data = path.read_bytes()
    if len(data) != size:
        raise ContractError("attachment size differs from native metadata")
    return data


def _named_attachments(ctx: Any, task_id: str) -> dict[str, bytes]:
    listed = _dispatch(ctx, "kanban_attachments", {"task_id": task_id})
    if listed.get("task_id") != task_id or listed.get("ok") is not True or not isinstance(listed.get("attachments"), list):
        raise ContractError("kanban attachment envelope is malformed")
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in listed["attachments"]:
        if not isinstance(item, dict):
            raise ContractError("attachment list item is malformed")
        name = item.get("filename")
        if name in HANDOFF_FILENAMES:
            if name in by_name:
                raise ContractError("required attachment filename is ambiguous")
            by_name[name] = item
    if set(by_name) != set(HANDOFF_FILENAMES):
        raise ContractError("exactly named handoff evidence attachments are required")
    return {name: _read_attachment(task_id, by_name[name]) for name in HANDOFF_FILENAMES}


def _evidence(ctx: Any, task_id: str) -> tuple[dict[str, Any], VerifiedHandoff, dict[str, bytes]]:
    task, _ = _show(ctx, task_id)
    attachments = _named_attachments(ctx, task_id)
    try:
        handoff = VerifiedHandoff.parse(json.loads(attachments["handoff.json"]))
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
        raise ContractError("handoff.json fails the v2 contract") from exc
    validate_evidence(handoff, attachments["sealed-plan.md"], attachments["caller-inventory.json"])
    return task, handoff, attachments


def _marker(body: str, label: str) -> dict[str, Any]:
    prefix, suffix = f"<!-- {label}\n", "\n-->"
    if not body.startswith(prefix) or not body.endswith(suffix):
        raise ContractError(f"required {label} marker is absent")
    try:
        value = json.loads(body[len(prefix):-len(suffix)])
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label} marker is malformed") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label} marker must contain an object")
    return value


def _root_marker(task: Mapping[str, Any]) -> dict[str, str]:
    marker = _marker(task["body"], ROOT_MARKER)
    if set(marker) != {"schema_version", "handoff_identity", "graph_identity"} or marker.get("schema_version") != 1:
        raise ContractError("verified-plan root marker schema is invalid")
    for key in ("handoff_identity", "graph_identity"):
        if not isinstance(marker.get(key), str) or not re_full_sha(marker[key]):
            raise ContractError("verified-plan root marker identity is invalid")
    return marker  # type: ignore[return-value]


def re_full_sha(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _unit_marker(task: Mapping[str, Any]) -> dict[str, str]:
    marker = _marker(task["body"], UNIT_MARKER)
    fields = {"schema_version", "handoff_identity", "graph_identity", "root_task_id", "unit_id", "role"}
    if set(marker) != fields or marker.get("schema_version") != 1 or marker.get("role") not in {"coddy", "tammy"}:
        raise ContractError("unit marker schema is invalid")
    for key in ("handoff_identity", "graph_identity", "root_task_id", "unit_id"):
        if not isinstance(marker.get(key), str) or not marker[key]:
            raise ContractError("unit marker identity is invalid")
    return marker  # type: ignore[return-value]


def preflight(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    _require_role(ctx, "quentin")
    task, handoff, _ = _evidence(ctx, _active_task_id())
    return {"ok": True, "task_id": task["id"], "project_id": task["project_id"], "handoff_identity": handoff.identity(), "graph_identity": handoff.graph_identity}


def materialize(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    _require_role(ctx, "sheila")
    root_id = _active_task_id()
    root, handoff, _ = _evidence(ctx, root_id)
    marker = _root_marker(root)
    if marker["handoff_identity"] != handoff.identity() or marker["graph_identity"] != handoff.graph_identity:
        raise ContractError("root marker is not bound to its immutable evidence")
    created: list[str] = []
    by_unit: dict[str, str] = {}
    for unit in handoff.units:
        if unit.owner not in {"coddy", "tammy"}:
            continue
        parents = [root_id] + [by_unit[parent] for parent in unit.parents if parent in by_unit]
        if len(parents) != 1 + len(unit.parents):
            raise ContractError("materialization topology is not dependency ordered")
        body = "<!-- " + UNIT_MARKER + "\n" + canonical_json({"schema_version": 1, "handoff_identity": handoff.identity(), "graph_identity": handoff.graph_identity, "root_task_id": root_id, "unit_id": unit.unit_id, "role": unit.owner}).decode() + "\n-->"
        idem = digest({"root_task_id": root_id, "handoff_identity": handoff.identity(), "unit_id": unit.unit_id})
        created_result = _dispatch(ctx, "kanban_create", {"title": f"Verified {unit.owner} unit {unit.unit_id}", "assignee": unit.owner, "body": body, "parents": parents, "project_id": root["project_id"], "idempotency_key": f"software-factory:{idem}"})
        child_id = created_result.get("task_id")
        if not isinstance(child_id, str) or not child_id:
            raise ContractError("kanban_create response is malformed")
        child, envelope = _show(ctx, child_id)
        if child.get("body") != body or child.get("assignee") != unit.owner or child.get("project_id") != root["project_id"] or envelope["parents"] != parents:
            raise ContractError("materialized task exact readback failed")
        by_unit[unit.unit_id] = child_id
        created.append(child_id)
    return {"ok": True, "root_task_id": root_id, "created_task_ids": created}


def _receipt(handoff_identity: str, unit_id: str, task_id: str, candidate_sha: str) -> dict[str, Any]:
    if not isinstance(candidate_sha, str) or len(candidate_sha) != 40 or any(ch not in "0123456789abcdef" for ch in candidate_sha):
        raise ContractError("candidate SHA must be full lowercase SHA40")
    core = {"schema_version": 1, "handoff_identity": handoff_identity, "implementation_unit": unit_id, "implementation_task_id": task_id, "candidate_sha": candidate_sha}
    return {**core, "candidate_receipt_id": digest(core)}


def publish(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    _require_role(ctx, "coddy")
    task_id = _active_task_id()
    task, _ = _show(ctx, task_id)
    marker = _unit_marker(task)
    if marker["role"] != "coddy":
        raise ContractError("candidate publisher task is not a coddy unit")
    worktree = task.get("workspace_path")
    if not isinstance(worktree, str) or not Path(worktree).is_dir():
        raise ContractError("candidate task has no persisted validated worktree")
    result = subprocess.run(["git", "-C", worktree, "rev-parse", "HEAD"], check=False, capture_output=True, text=True, timeout=15)
    candidate_sha = result.stdout.strip()
    if result.returncode != 0:
        raise ContractError("fixed git candidate inspection failed")
    receipt = _receipt(marker["handoff_identity"], marker["unit_id"], task_id, candidate_sha)
    payload = canonical_json(receipt)
    listed = _dispatch(ctx, "kanban_attachments", {"task_id": task_id})
    if not isinstance(listed.get("attachments"), list):
        raise ContractError("candidate attachment inventory is malformed")
    for item in listed["attachments"]:
        if isinstance(item, dict) and item.get("filename") == RECEIPT_FILENAME and _read_attachment(task_id, item) == payload:
            return {"ok": True, "receipt": receipt, "idempotent": True}
    attach = _dispatch(ctx, "kanban_attach", {"task_id": task_id, "filename": RECEIPT_FILENAME, "content_type": "application/json", "content_base64": base64.b64encode(payload).decode()})
    if attach.get("task_id") != task_id or not isinstance(attach.get("attachment_id"), int):
        raise ContractError("candidate receipt attach response is malformed")
    readback = _dispatch(ctx, "kanban_attachments", {"task_id": task_id})
    if not any(isinstance(item, dict) and item.get("filename") == RECEIPT_FILENAME and _read_attachment(task_id, item) == payload for item in readback.get("attachments", [])):
        raise ContractError("candidate receipt exact attachment readback failed")
    return {"ok": True, "receipt": receipt, "idempotent": False}


def validate(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    _require_role(ctx, "tammy")
    task_id = _active_task_id()
    task, envelope = _show(ctx, task_id)
    marker = _unit_marker(task)
    if marker["role"] != "tammy" or len(envelope["parents"]) < 2:
        raise ContractError("validator task topology is invalid")
    coddy_id = next((parent for parent in envelope["parents"] if isinstance(parent, str) and parent != marker["root_task_id"]), None)
    if not isinstance(coddy_id, str):
        raise ContractError("validator has no candidate parent")
    coddy, _ = _show(ctx, coddy_id)
    coddy_marker = _unit_marker(coddy)
    if coddy_marker["role"] != "coddy" or any(coddy_marker[key] != marker[key] for key in ("handoff_identity", "graph_identity", "root_task_id")):
        raise ContractError("candidate task does not belong to validator graph")
    root, handoff, _ = _evidence(ctx, marker["root_task_id"])
    root_marker = _root_marker(root)
    if root.get("project_id") != task.get("project_id") or root_marker["handoff_identity"] != marker["handoff_identity"] or handoff.identity() != marker["handoff_identity"] or handoff.graph_identity != marker["graph_identity"]:
        raise ContractError("validator policy, task, project, or graph does not match")
    listed = _dispatch(ctx, "kanban_attachments", {"task_id": coddy_id})
    matches = [item for item in listed.get("attachments", []) if isinstance(item, dict) and item.get("filename") == RECEIPT_FILENAME]
    if len(matches) != 1:
        raise ContractError("candidate receipt attachment must exist exactly once")
    try:
        receipt = parse_candidate_receipt(json.loads(_read_attachment(coddy_id, matches[0])))
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
        raise ContractError("candidate receipt attachment is malformed") from exc
    if receipt["handoff_identity"] != handoff.identity() or receipt["implementation_unit"] != coddy_marker["unit_id"] or receipt["implementation_task_id"] != coddy_id or not any(p.implementation_unit == receipt["implementation_unit"] and p.verifier_unit == marker["unit_id"] for p in handoff.candidate_policy):
        raise ContractError("candidate receipt violates policy or graph")
    return {"ok": True, "valid": True, "receipt": receipt}


def read_receipt(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    role = _profile(ctx)
    if role not in ROLES:
        raise ContractError("software-factory receipt reader requires an authorized runtime profile")
    task_id = _active_task_id()
    listed = _dispatch(ctx, "kanban_attachments", {"task_id": task_id})
    matches = [item for item in listed.get("attachments", []) if isinstance(item, dict) and item.get("filename") == RECEIPT_FILENAME]
    if len(matches) != 1:
        raise ContractError("candidate receipt attachment must exist exactly once")
    return {"ok": True, "receipt": parse_candidate_receipt(json.loads(_read_attachment(task_id, matches[0])))}


def _raw_create_guard(ctx: Any, tool_name: str, _args: Mapping[str, Any] | None = None, **_: Any) -> dict[str, str] | None:
    if tool_name != "kanban_create" or _profile(ctx) != "sheila":
        return None
    try:
        task, _ = _show(ctx, _active_task_id())
        _root_marker(task)
    except (ContractError, OSError):
        return None
    return {"action": "block", "message": "verified-plan roots may materialize topology only through software_factory_materialize_verified_plan"}


def register(ctx: Any) -> None:
    role = _profile(ctx)
    handlers: dict[str, Callable[[Any, Mapping[str, Any]], dict[str, Any]]] = {"software_factory_preflight_verified_plan": preflight, "software_factory_materialize_verified_plan": materialize, "software_factory_publish_candidate_receipt": publish, "software_factory_validate_candidate_receipt": validate, "software_factory_read_receipt": read_receipt}
    for name in ROLES.get(role, set()):
        def bound(args: Mapping[str, Any], _handler: Callable[[Any, Mapping[str, Any]], dict[str, Any]] = handlers[name], **_: Any) -> str:
            return json.dumps(_handler(ctx, args), sort_keys=True)
        ctx.register_tool(name=name, toolset=TOOLSET, schema=SCHEMAS[name], handler=bound)
    if role == "sheila":
        ctx.register_hook("pre_tool_call", lambda tool_name, args=None, **kwargs: _raw_create_guard(ctx, tool_name, args, **kwargs))
