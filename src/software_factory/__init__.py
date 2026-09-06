"""Hermes plugin registration and strictly bounded Kanban handlers."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from .plan_pipeline import make_receipt
from .validation import (
    MARKER,
    POLICY,
    RECEIPT,
    decode_marker,
    receipt_body,
    validate_plan,
    verify_attachment,
)

TOOLSET = "software-factory"
ROLES = {
    "sheila": {"software_factory_preflight_verified_plan", "software_factory_materialize_verified_plan", "software_factory_read_receipt"},
    "coddy": {"software_factory_preflight_verified_plan", "software_factory_publish_candidate_receipt", "software_factory_read_receipt"},
    "tammy": {"software_factory_validate_candidate_receipt", "software_factory_read_receipt"},
}


def _schema(name: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": "Fail-closed software factory operation.", "parameters": {
        "type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}

SCHEMAS = {
    "software_factory_preflight_verified_plan": _schema("software_factory_preflight_verified_plan", {}),
    "software_factory_materialize_verified_plan": _schema("software_factory_materialize_verified_plan", {}),
    "software_factory_publish_candidate_receipt": _schema("software_factory_publish_candidate_receipt", {"candidate_sha": {"type": "string"}}, ["candidate_sha"]),
    "software_factory_validate_candidate_receipt": _schema("software_factory_validate_candidate_receipt", {"receipt": {"type": "object", "additionalProperties": False, "properties": {"policy": {"type": "string"}, "task_id": {"type": "string"}, "project_id": {"type": "string"}, "graph_id": {"type": "string"}, "candidate_sha": {"type": "string"}}, "required": ["policy", "task_id", "project_id", "graph_id", "candidate_sha"]}}, ["receipt"]),
    "software_factory_read_receipt": _schema("software_factory_read_receipt", {}),
}


def _profile() -> str:
    configured = os.environ.get("HERMES_PROFILE_NAME") or os.environ.get("HERMES_PROFILE")
    if configured:
        return configured.strip().lower()
    try:
        import importlib

        profiles = importlib.import_module("hermes_cli.profiles")
        return str(profiles.get_active_profile_name()).lower()
    except (ImportError, RuntimeError):
        return ""


def _task_id() -> str:
    value = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if not value:
        raise ValueError("factory tools require an active Kanban task")
    return value


def _dispatch(ctx: Any, name: str, args: Mapping[str, Any]) -> Any:
    # Deliberately fixed native names; no caller-controlled tool or path reaches dispatch.
    if name not in {"kanban_show", "kanban_attachments", "kanban_create"}:
        raise ValueError("unsupported native Kanban operation")
    return ctx.dispatch_tool(name, dict(args))


def _task(ctx: Any) -> Mapping[str, Any]:
    value = _dispatch(ctx, "kanban_show", {"task_id": _task_id()})
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not isinstance(value.get("project_id"), str):
        raise ValueError("active task readback is malformed")
    return value


def _metadata_list(ctx: Any, task_id: str) -> list[Mapping[str, Any]]:
    value = _dispatch(ctx, "kanban_attachments", {"task_id": task_id, "include_bytes": False})
    items = value.get("attachments") if isinstance(value, dict) else value
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("Kanban attachment inventory is malformed")
    return items


def _plan(ctx: Any) -> tuple[Mapping[str, Any], dict[str, Any]]:
    task = _task(ctx)
    plan = validate_plan(decode_marker(task.get("body"), MARKER), task_id=task["id"], project_id=task["project_id"])
    metadata = _metadata_list(ctx, task["id"])
    by_id = {item.get("attachment_id"): item for item in metadata}
    for entry in plan["inventory"]:
        item = by_id.get(entry["attachment_id"])
        if item is None:
            raise ValueError("planned attachment is not contained by active task")
        # Containment and every persisted metadata field are checked before bytes load.
        if any(item.get(key) != entry[key] for key in ("attachment_id", "filename", "sha256", "size")):
            raise ValueError("attachment metadata differs from validated inventory")
        loaded = _dispatch(ctx, "kanban_attachments", {"task_id": task["id"], "attachment_id": entry["attachment_id"], "include_bytes": True})
        payload = loaded.get("bytes") if isinstance(loaded, dict) else None
        if not isinstance(payload, bytes):
            raise ValueError("Kanban attachment bytes are malformed")
        verify_attachment(entry, item, payload)
    return task, plan


def preflight(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    task, plan = _plan(ctx)
    return {"ok": True, "policy": POLICY, "task_id": task["id"], "project_id": task["project_id"], "graph_id": plan["graph_id"]}


def materialize(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    task, plan = _plan(ctx)
    body = "<!-- SOFTWARE-FACTORY-VERIFIED-PLAN\n" + plan["graph_id"] + "\n-->"
    existing = _dispatch(ctx, "kanban_show", {"task_id": task["id"], "factory_graph_id": plan["graph_id"]})
    if isinstance(existing, dict) and existing.get("body") == body and isinstance(existing.get("id"), str):
        return {"ok": True, "task_id": existing["id"], "idempotent": True}
    created = _dispatch(ctx, "kanban_create", {"title": "Verified plan", "body": body, "parent_task_id": task["id"]})
    created_id = created.get("id") if isinstance(created, dict) else None
    readback = _dispatch(ctx, "kanban_show", {"task_id": created_id})
    if not isinstance(created_id, str) or not isinstance(readback, dict) or readback.get("id") != created_id or readback.get("body") != body:
        raise ValueError("materialized plan exact readback failed")
    return {"ok": True, "task_id": created_id, "idempotent": False}


def publish(ctx: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    task, plan = _plan(ctx)
    receipt = make_receipt(task_id=task["id"], project_id=task["project_id"], graph_id=plan["graph_id"], candidate_sha=args["candidate_sha"])
    return {"ok": True, "receipt": receipt, "body": receipt_body(receipt)}


def validate(ctx: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    task, plan = _plan(ctx)
    receipt = args["receipt"]
    expected = {"policy": POLICY, "task_id": task["id"], "project_id": task["project_id"], "graph_id": plan["graph_id"]}
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("receipt has wrong policy, task identity, project, or graph")
    make_receipt(
        task_id=receipt["task_id"],
        project_id=receipt["project_id"],
        graph_id=receipt["graph_id"],
        candidate_sha=receipt["candidate_sha"],
    )
    return {"ok": True, "valid": True}


def read_receipt(ctx: Any, _args: Mapping[str, Any]) -> dict[str, Any]:
    task = _task(ctx)
    receipt = decode_marker(task.get("body"), RECEIPT)
    return {"ok": True, "receipt": receipt}


def _raw_create_guard(tool_name: str, args: Mapping[str, Any] | None = None, **_: Any) -> dict[str, str] | None:
    if tool_name != "kanban_create":
        return None
    body = (args or {}).get("body")
    if isinstance(body, str) and body.startswith("<!-- SOFTWARE-FACTORY-VERIFIED-PLAN\n"):
        return None
    return {"action": "block", "message": "factory verified-plan roots require the raw factory marker body"}


def register(ctx: Any) -> None:
    handlers: dict[str, Callable[[Any, Mapping[str, Any]], dict[str, Any]]] = {
        "software_factory_preflight_verified_plan": preflight, "software_factory_materialize_verified_plan": materialize,
        "software_factory_publish_candidate_receipt": publish, "software_factory_validate_candidate_receipt": validate,
        "software_factory_read_receipt": read_receipt}
    for name in ROLES.get(_profile(), set()):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=SCHEMAS[name], handler=handlers[name])
    ctx.register_hook("pre_tool_call", _raw_create_guard)
