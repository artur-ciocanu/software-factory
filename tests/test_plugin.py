from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import software_factory as factory
from software_factory.validation import ROOT_MARKER, VerifiedHandoff


def handoff_payload(sealed: bytes, inventory: bytes) -> dict:
    return {
        "schema_version": 2,
        "requested_by": "quentin",
        "board_slug": "factory",
        "capability_requirements": [
            {"profile": role, "capabilities": ["kanban"]} for role in ("coddy", "tammy", "ferris")
        ],
        "verified_plan": {
            "plan_id": "plan-a",
            "logical_project_slug": "factory",
            "implementation_base_sha": "a" * 40,
            "sealed_plan_sha256": hashlib.sha256(sealed).hexdigest(),
            "sealed_plan_size": len(sealed),
            "caller_manifest": {
                "inventory_sha256": hashlib.sha256(inventory).hexdigest(),
                "declared_complete": True,
                "entries": [{"caller": "pkg.main", "kind": "production", "disposition": "replaced", "owning_execution_unit": "impl"}],
            },
            "topology": [
                {"unit_id": "impl", "owner": "coddy", "phases": [{"name": "build", "shares_card": False, "shares_worktree": False, "shares_branch": False, "shares_candidate": False, "shares_verifier": False}], "parents": []},
                {"unit_id": "verify", "owner": "tammy", "phases": [{"name": "verify", "shares_card": False, "shares_worktree": False, "shares_branch": False, "shares_candidate": False, "shares_verifier": False}], "parents": ["impl"]},
            ],
            "candidate_policy": [{"implementation_unit": "impl", "verifier_unit": "verify", "verifier": "tammy"}],
            "supersession": {"graph_identity": "b" * 64, "predecessor_graph_identity": None, "recovery_attempt": 0},
        },
    }


class Dispatch:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, dict]] = []
        inventory = b'{"schema_version":1,"active_callers":[{"caller":"pkg.main","kind":"production"}]}'
        sealed = b"sealed plan"
        self.files = {"handoff.json": json.dumps(handoff_payload(sealed, inventory)).encode(), "sealed-plan.md": sealed, "caller-inventory.json": inventory}
        self.tasks = {"T1": {"id": "T1", "project_id": "project-1", "body": "source", "assignee": "quentin", "workspace_path": None}}
        self.parents = {"T1": []}
        self.attachments = {"T1": []}
        for name, data in self.files.items():
            self.add_attachment("T1", name, data)

    def add_attachment(self, task_id: str, filename: str, data: bytes) -> None:
        directory = self.root / task_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_bytes(data)
        self.attachments.setdefault(task_id, []).append({"id": len(self.attachments[task_id]) + 1, "filename": filename, "content_type": "application/json", "size": len(data), "stored_path": str(path)})

    def dispatch_tool(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if name == "kanban_show":
            task_id = args["task_id"]
            return json.dumps({"task": self.tasks[task_id], "parents": self.parents[task_id], "children": []})
        if name == "kanban_attachments":
            return json.dumps({"ok": True, "task_id": args["task_id"], "attachments": self.attachments[args["task_id"]]})
        if name == "kanban_create":
            task_id = f"C{len(self.tasks)}"
            self.tasks[task_id] = {"id": task_id, "project_id": args["project_id"], "body": args["body"], "assignee": args["assignee"], "workspace_path": None}
            self.parents[task_id] = args["parents"]
            self.attachments[task_id] = []
            return json.dumps({"ok": True, "task_id": task_id})
        if name == "kanban_attach":
            import base64
            self.add_attachment(args["task_id"], args["filename"], base64.b64decode(args["content_base64"]))
            return json.dumps({"ok": True, "task_id": args["task_id"], "attachment_id": len(self.attachments[args["task_id"]])})
        raise AssertionError(name)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dispatch:
    monkeypatch.setenv("HERMES_KANBAN_TASK", "T1")
    monkeypatch.setenv("HERMES_KANBAN_ATTACHMENTS_ROOT", str(tmp_path / "attachments"))
    return Dispatch(tmp_path / "attachments")


def test_preflight_uses_json_string_native_envelopes_and_attachment_paths(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    result = factory.preflight(ctx, {})
    assert result["ok"] is True
    assert {name for name, _ in ctx.calls} == {"kanban_show", "kanban_attachments"}
    assert all("include_bytes" not in args for _, args in ctx.calls)


def test_wrong_profile_fails_closed_before_native_calls(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "coddy")
    with pytest.raises(ValueError, match="requires quentin"):
        factory.preflight(ctx, {})
    assert ctx.calls == []


def test_rejects_attachment_outside_active_task_root(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    ctx.attachments["T1"][0]["stored_path"] = str(tmp_path / "outside")
    (tmp_path / "outside").write_bytes(ctx.files["handoff.json"])
    with pytest.raises(ValueError, match="outside"):
        factory.preflight(ctx, {})


def test_materialize_creates_native_topology_with_exact_readback(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "sheila")
    handoff = VerifiedHandoff.parse(json.loads(ctx.files["handoff.json"]))
    ctx.tasks["T1"]["body"] = "<!-- " + ROOT_MARKER + "\n" + json.dumps({"schema_version": 1, "handoff_identity": handoff.identity(), "graph_identity": handoff.graph_identity}) + "\n-->"
    assert factory.materialize(ctx, {})["created_task_ids"] == ["C1", "C2"]
    creates = [args for name, args in ctx.calls if name == "kanban_create"]
    assert all({"title", "assignee", "body", "parents", "project_id", "idempotency_key"} <= set(args) for args in creates)


def test_publish_then_validate_candidate_receipt_with_read_only_tammy(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    handoff = VerifiedHandoff.parse(json.loads(ctx.files["handoff.json"]))
    ctx.tasks["T1"]["body"] = "<!-- " + ROOT_MARKER + "\n" + json.dumps({"schema_version": 1, "handoff_identity": handoff.identity(), "graph_identity": handoff.graph_identity}) + "\n-->"
    monkeypatch.setenv("HERMES_PROFILE", "sheila")
    factory.materialize(ctx, {})
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    for command in (["git", "init"], ["git", "config", "user.email", "test@example.invalid"], ["git", "config", "user.name", "Test"], ["git", "commit", "--allow-empty", "-m", "candidate"]):
        subprocess.run(command, cwd=worktree, check=True, capture_output=True)
    ctx.tasks["C1"]["workspace_path"] = str(worktree)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "C1")
    monkeypatch.setenv("HERMES_PROFILE", "coddy")
    receipt = factory.publish(ctx, {})
    assert receipt["idempotent"] is False
    assert factory.publish(ctx, {})["idempotent"] is True
    ctx.calls.clear()
    monkeypatch.setenv("HERMES_KANBAN_TASK", "C2")
    monkeypatch.setenv("HERMES_PROFILE", "tammy")
    assert factory.validate(ctx, {})["valid"] is True
    assert {name for name, _ in ctx.calls} <= {"kanban_show", "kanban_attachments"}


    class Registry:
        profile_name = "tammy"
        def __init__(self) -> None: self.tools: list[dict] = []
        def register_tool(self, **kwargs): self.tools.append(kwargs)
        def register_hook(self, *_args): raise AssertionError("tammy must not register hook")
    registry = Registry()
    factory.register(registry)
    assert {tool["name"] for tool in registry.tools} == {"software_factory_validate_candidate_receipt", "software_factory_read_receipt"}
    assert callable(registry.tools[0]["handler"])


def test_sheila_raw_create_guard_only_blocks_verified_root(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "sheila")
    assert factory._raw_create_guard(ctx, "kanban_create", {}) is None
    handoff = VerifiedHandoff.parse(json.loads(ctx.files["handoff.json"]))
    ctx.tasks["T1"]["body"] = "<!-- " + ROOT_MARKER + "\n" + json.dumps({"schema_version": 1, "handoff_identity": handoff.identity(), "graph_identity": handoff.graph_identity}) + "\n-->"
    assert factory._raw_create_guard(ctx, "kanban_create", {})["action"] == "block"


def test_package_compiles_with_hermes_python_when_present() -> None:
    python = Path("/Users/ciocanu/.hermes/hermes-agent/.venv/bin/python")
    executable = str(python) if python.exists() else sys.executable
    package = Path(factory.__file__).resolve().parent
    result = subprocess.run([executable, "-m", "compileall", "-q", str(package)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_package_has_no_profile_literal_path_or_shell() -> None:
    package = Path(factory.__file__).resolve().parent
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "/Users/" not in source and "profiles/" not in source and "shell=True" not in source
