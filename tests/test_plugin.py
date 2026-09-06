from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import software_factory as factory
from software_factory.validation import ROOT_MARKER, VerifiedHandoff, graph_identity


def handoff_payload(sealed: bytes, inventory: bytes) -> dict:
    payload = {
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
                {"unit_id": "integrate", "owner": "ferris", "phases": [{"name": "integrate", "shares_card": False, "shares_worktree": False, "shares_branch": False, "shares_candidate": False, "shares_verifier": False}], "parents": ["verify"]},
            ],
            "candidate_policy": [{"implementation_unit": "impl", "verifier_unit": "verify", "verifier": "tammy"}],
            "supersession": {"graph_identity": "", "predecessor_graph_identity": None, "recovery_attempt": 0},
        },
    }
    refresh_graph_identity(payload)
    return payload


def refresh_graph_identity(payload: dict) -> None:
    payload["verified_plan"]["supersession"]["graph_identity"] = graph_identity(payload)


class Dispatch:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, dict]] = []
        self.cli_calls: list[tuple[list[str], dict[str, object]]] = []
        inventory = b'{"schema_version":1,"active_callers":[{"caller":"pkg.main","kind":"production"}]}'
        sealed = b"sealed plan"
        self.files = {"handoff.json": json.dumps(handoff_payload(sealed, inventory)).encode(), "sealed-plan.md": sealed, "caller-inventory.json": inventory}
        self.tasks = {"T1": {"id": "T1", "body": "source", "assignee": "quentin", "workspace_kind": None, "workspace_path": None, "status": "open"}}
        self.cli_tasks = {"T1": {**self.tasks["T1"], "project_id": "project-1"}}
        self.parents = {"T1": []}
        self.attachments = {"T1": []}
        self.idempotency: dict[str, str] = {}
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
            if args["idempotency_key"] in self.idempotency:
                return json.dumps({"ok": True, "task_id": self.idempotency[args["idempotency_key"]]})
            task_id = f"C{len(self.tasks)}"
            self.tasks[task_id] = {"id": task_id, "body": args["body"], "assignee": args["assignee"], "workspace_kind": None, "workspace_path": None, "status": "open"}
            self.cli_tasks[task_id] = {**self.tasks[task_id], "project_id": args["project_id"]}
            self.parents[task_id] = args["parents"]
            self.attachments[task_id] = []
            self.idempotency[args["idempotency_key"]] = task_id
            return json.dumps({"ok": True, "task_id": task_id})
        if name == "kanban_attach":
            self.add_attachment(args["task_id"], args["filename"], base64.b64decode(args["content_base64"]))
            return json.dumps({"ok": True, "task_id": args["task_id"], "attachment_id": len(self.attachments[args["task_id"]])})
        raise AssertionError(name)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dispatch:
    monkeypatch.setenv("HERMES_KANBAN_TASK", "T1")
    monkeypatch.setenv("HERMES_KANBAN_ATTACHMENTS_ROOT", str(tmp_path / "attachments"))
    dispatch = Dispatch(tmp_path / "attachments")
    original_run = subprocess.run

    def run(args, **kwargs):
        if args[0] == "hermes":
            dispatch.cli_calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, json.dumps(dispatch.cli_tasks[args[3]]), "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(factory.subprocess, "run", run)
    return dispatch


def test_preflight_uses_json_string_native_envelopes_and_attachment_paths(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    result = factory.preflight(ctx, {})
    assert result["ok"] is True
    assert {name for name, _ in ctx.calls} == {"kanban_show", "kanban_attachments"}
    assert all("include_bytes" not in args for _, args in ctx.calls)
    assert ctx.cli_calls == [(["hermes", "kanban", "show", "T1", "--json"], {"check": False, "capture_output": True, "text": True, "timeout": 15})]


def test_profile_falls_back_to_named_hermes_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_PROFILE_NAME", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/factory/profiles/tammy")
    assert factory._profile() == "tammy"


def test_wrong_profile_fails_closed_before_native_calls(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "coddy")
    with pytest.raises(ValueError, match="requires quentin"):
        factory.preflight(ctx, {})
    assert ctx.calls == []


def test_rejects_cli_task_mismatch(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    ctx.cli_tasks["T1"]["status"] = "done"
    with pytest.raises(ValueError, match="native and CLI task status differ"):
        factory.preflight(ctx, {})


def test_rejects_cli_task_without_project_id(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    del ctx.cli_tasks["T1"]["project_id"]
    with pytest.raises(ValueError, match="active task has no project_id"):
        factory.preflight(ctx, {})


def test_rejects_malformed_cli_json(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")

    def malformed_cli(args, **kwargs):
        assert args == ["hermes", "kanban", "show", "T1", "--json"]
        assert kwargs == {"check": False, "capture_output": True, "text": True, "timeout": 15}
        return subprocess.CompletedProcess(args, 0, "{", "")

    monkeypatch.setattr(factory.subprocess, "run", malformed_cli)
    with pytest.raises(ValueError, match="malformed JSON"):
        factory.preflight(ctx, {})


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
    ctx.cli_tasks["T1"]["body"] = ctx.tasks["T1"]["body"]
    assert factory.materialize(ctx, {})["created_task_ids"] == ["C1", "C2", "C3"]
    creates = [args for name, args in ctx.calls if name == "kanban_create"]
    assert all({"title", "assignee", "body", "parents", "project_id", "idempotency_key"} <= set(args) for args in creates)


def test_materialize_includes_ferris_terminal_fan_in_and_is_idempotent(
    ctx: Dispatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(ctx.files["handoff.json"])
    handoff_bytes = json.dumps(payload).encode()
    ctx.files["handoff.json"] = handoff_bytes
    handoff_attachment = next(item for item in ctx.attachments["T1"] if item["filename"] == "handoff.json")
    handoff_attachment["size"] = len(handoff_bytes)
    (ctx.root / "T1" / "handoff.json").write_bytes(handoff_bytes)
    handoff = VerifiedHandoff.parse(payload)
    ctx.tasks["T1"]["body"] = factory._verified_root_body(handoff)
    ctx.cli_tasks["T1"]["body"] = ctx.tasks["T1"]["body"]
    monkeypatch.setenv("HERMES_PROFILE", "sheila")

    assert factory.materialize(ctx, {})["created_task_ids"] == ["C1", "C2", "C3"]
    assert ctx.tasks["C3"]["assignee"] == "ferris"
    assert ctx.parents["C3"] == ["T1", "C2"]
    ferris_marker = factory._unit_marker(ctx.tasks["C3"])
    assert ferris_marker == {
        "schema_version": 1,
        "handoff_identity": handoff.identity(),
        "graph_identity": handoff.graph_identity,
        "root_task_id": "T1",
        "unit_id": "integrate",
        "role": "ferris",
    }
    first_creates = [args for name, args in ctx.calls if name == "kanban_create"]

    assert factory.materialize(ctx, {})["created_task_ids"] == ["C1", "C2", "C3"]
    all_creates = [args for name, args in ctx.calls if name == "kanban_create"]
    assert len(ctx.tasks) == 4
    assert all_creates[3:] == first_creates


def test_verified_handoff_accepts_valid_canonical_graph(ctx: Dispatch) -> None:
    payload = json.loads(ctx.files["handoff.json"])

    handoff = VerifiedHandoff.parse(payload)

    assert handoff.graph_identity == graph_identity(payload)
    assert handoff.identity() == handoff.graph_identity


def test_verified_handoff_rejects_altered_graph_identity(ctx: Dispatch) -> None:
    payload = json.loads(ctx.files["handoff.json"])
    payload["verified_plan"]["supersession"]["graph_identity"] = "a" * 64

    with pytest.raises(ValueError, match="declared graph identity"):
        VerifiedHandoff.parse(payload)


def test_verified_handoff_rejects_topology_cycle_even_with_matching_identity(ctx: Dispatch) -> None:
    payload = json.loads(ctx.files["handoff.json"])
    payload["verified_plan"]["topology"][0]["parents"] = ["integrate"]
    refresh_graph_identity(payload)

    with pytest.raises(ValueError, match="contains a cycle"):
        VerifiedHandoff.parse(payload)


def test_verified_handoff_requires_one_ferris_terminal(ctx: Dispatch) -> None:
    payload = json.loads(ctx.files["handoff.json"])
    payload["verified_plan"]["topology"].pop()
    refresh_graph_identity(payload)

    with pytest.raises(ValueError, match="exactly one ferris terminal"):
        VerifiedHandoff.parse(payload)


def test_verified_handoff_requires_ferris_fan_in_from_every_selected_tammy(ctx: Dispatch) -> None:
    payload = json.loads(ctx.files["handoff.json"])
    payload["verified_plan"]["topology"].append(
        {
            "unit_id": "followup",
            "owner": "coddy",
            "phases": [{"name": "remediate", "shares_card": False, "shares_worktree": False, "shares_branch": False, "shares_candidate": False, "shares_verifier": False}],
            "parents": ["verify"],
        }
    )
    payload["verified_plan"]["topology"][2]["parents"] = ["followup"]
    refresh_graph_identity(payload)

    with pytest.raises(ValueError, match="depend on every selected tammy verifier"):
        VerifiedHandoff.parse(payload)


def test_quentin_publishes_exact_idempotent_verified_root(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    result = factory.publish_verified_root(ctx, {})
    assert result["root_task_id"] == "C1"
    assert result["idempotent"] is False
    assert result["root_task"]["assignee"] == "sheila"
    assert result["parents"] == ["T1"]
    assert set(result["attachments"]) == {"handoff.json", "sealed-plan.md", "caller-inventory.json"}
    assert ctx.tasks["C1"]["body"] == factory._verified_root_body(
        VerifiedHandoff.parse(json.loads(ctx.files["handoff.json"]))
    )
    assert {item["filename"] for item in ctx.attachments["C1"]} == set(ctx.files)
    assert all(
        (ctx.root / "C1" / name).read_bytes() == data for name, data in ctx.files.items()
    )
    creates = [args for name, args in ctx.calls if name == "kanban_create"]
    assert creates == [
        {
            "title": "Verified plan plan-a",
            "assignee": "sheila",
            "body": ctx.tasks["C1"]["body"],
            "parents": ["T1"],
            "project_id": "project-1",
            "idempotency_key": creates[0]["idempotency_key"],
        }
    ]
    assert factory.publish_verified_root(ctx, {})["idempotent"] is True
    assert len([args for name, args in ctx.calls if name == "kanban_create"]) == 2
    assert len(ctx.attachments["C1"]) == 3


def test_quentin_publish_fails_before_create_for_invalid_evidence(
    ctx: Dispatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    ctx.files["sealed-plan.md"] = b"tampered"
    (ctx.root / "T1" / "sealed-plan.md").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="attachment size differs"):
        factory.publish_verified_root(ctx, {})
    assert not any(name == "kanban_create" for name, _ in ctx.calls)
    assert len(ctx.tasks) == 1


def test_quentin_publish_does_not_repair_partial_root(
    ctx: Dispatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "quentin")
    ctx.add_attachment("C1", "handoff.json", ctx.files["handoff.json"])
    handoff = VerifiedHandoff.parse(json.loads(ctx.files["handoff.json"]))
    body = factory._verified_root_body(handoff)
    ctx.tasks["C1"] = {"id": "C1", "body": body, "assignee": "sheila", "workspace_kind": None, "workspace_path": None, "status": "open"}
    ctx.cli_tasks["C1"] = {**ctx.tasks["C1"], "project_id": "project-1"}
    ctx.parents["C1"] = ["T1"]
    identity = factory.digest(
        {
            "source_task_id": "T1",
            "project_id": "project-1",
            "handoff_identity": handoff.identity(),
            "graph_identity": handoff.graph_identity,
        }
    )
    ctx.idempotency = {f"software-factory:verified-root:{identity}": "C1"}
    with pytest.raises(ValueError, match="incomplete immutable evidence"):
        factory.publish_verified_root(ctx, {})
    assert len(ctx.attachments["C1"]) == 1
    assert not any(name == "kanban_attach" for name, _ in ctx.calls)


def test_publish_then_validate_candidate_receipt_with_read_only_tammy(ctx: Dispatch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    handoff = VerifiedHandoff.parse(json.loads(ctx.files["handoff.json"]))
    ctx.tasks["T1"]["body"] = "<!-- " + ROOT_MARKER + "\n" + json.dumps({"schema_version": 1, "handoff_identity": handoff.identity(), "graph_identity": handoff.graph_identity}) + "\n-->"
    ctx.cli_tasks["T1"]["body"] = ctx.tasks["T1"]["body"]
    monkeypatch.setenv("HERMES_PROFILE", "sheila")
    factory.materialize(ctx, {})
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    for command in (["git", "init"], ["git", "config", "user.email", "test@example.invalid"], ["git", "config", "user.name", "Test"], ["git", "commit", "--allow-empty", "-m", "candidate"]):
        subprocess.run(command, cwd=worktree, check=True, capture_output=True)
    ctx.tasks["C1"]["workspace_path"] = str(worktree)
    ctx.cli_tasks["C1"]["workspace_path"] = str(worktree)
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
    ctx.cli_tasks["T1"]["body"] = ctx.tasks["T1"]["body"]
    guard = factory._raw_create_guard(ctx, "kanban_create", {})
    assert guard is not None
    assert guard["action"] == "block"


def test_registration_is_exact_for_all_profiles() -> None:
    class Registry:
        def __init__(self, profile_name: str) -> None:
            self.profile_name = profile_name
            self.tools: list[dict] = []
            self.hooks: list[str] = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

        def register_hook(self, name, _handler):
            self.hooks.append(name)

    expected = {
        "quentin": {"software_factory_preflight_verified_plan", "software_factory_publish_verified_root", "software_factory_read_receipt"},
        "sheila": {"software_factory_materialize_verified_plan", "software_factory_read_receipt"},
        "coddy": {"software_factory_publish_candidate_receipt", "software_factory_read_receipt"},
        "tammy": {"software_factory_validate_candidate_receipt", "software_factory_read_receipt"},
        "mathew": set(),
        "ferris": set(),
    }
    for profile, names in expected.items():
        registry = Registry(profile)
        factory.register(registry)
        assert {tool["name"] for tool in registry.tools} == names
        assert all(tool["schema"] == factory.SCHEMAS[tool["name"]] for tool in registry.tools)
        assert registry.hooks == (["pre_tool_call"] if profile in {"quentin", "sheila"} else [])


def test_ferris_registration_has_no_software_factory_authority() -> None:
    class Registry:
        profile_name = "ferris"

        def __init__(self) -> None:
            self.tools: list[dict] = []
            self.hooks: list[str] = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

        def register_hook(self, name, _handler):
            self.hooks.append(name)

    registry = Registry()
    factory.register(registry)
    assert registry.tools == []
    assert registry.hooks == []


def test_candidate_policy_does_not_allow_ferris_implementation_units(ctx: Dispatch) -> None:
    payload = json.loads(ctx.files["handoff.json"])
    payload["verified_plan"]["topology"][0]["owner"] = "ferris"
    with pytest.raises(ValueError, match="candidate policy does not bind independent verifier"):
        VerifiedHandoff.parse(payload)


def test_package_compiles_with_configured_hermes_python() -> None:
    executable = os.environ.get("HERMES_PYTHON", sys.executable)
    package = Path(factory.__file__).resolve().parent
    result = subprocess.run([executable, "-m", "compileall", "-q", str(package)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_package_has_no_profile_literal_path_or_shell() -> None:
    package = Path(factory.__file__).resolve().parent
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "/Users/" not in source and "profiles/" not in source and "shell=True" not in source
