from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import software_factory as factory
from software_factory.validation import POLICY


class Dispatch:
    def __init__(self, payload: bytes = b"evidence") -> None:
        self.payload = payload
        digest = hashlib.sha256(payload).hexdigest()
        self.plan = {"policy": POLICY, "task_id": "T1", "project_id": "P1", "graph_id": "G1", "inventory": [{"attachment_id": 7, "filename": "evidence.json", "sha256": digest, "size": len(payload)}]}
        self.task = {"id": "T1", "project_id": "P1", "body": "<!-- SOFTWARE-FACTORY-PLAN\n" + json.dumps(self.plan) + "\n-->"}
        self.calls: list[tuple[str, dict]] = []
        self.created: dict | None = None

    def dispatch_tool(self, name: str, args: dict):
        self.calls.append((name, args))
        if name == "kanban_show":
            if args.get("factory_graph_id") and self.created:
                return self.created
            if args.get("task_id") == "C1":
                return self.created or {}
            return self.task
        if name == "kanban_attachments":
            item = self.plan["inventory"][0]
            if args["include_bytes"]:
                return {"bytes": self.payload}
            return {"attachments": [item]}
        if name == "kanban_create":
            self.created = {"id": "C1", "body": args["body"]}
            return {"id": "C1"}
        raise AssertionError(name)


@pytest.fixture(autouse=True)
def active_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_KANBAN_TASK", "T1")


def test_successful_basic_preflight() -> None:
    assert factory.preflight(Dispatch(), {}) == {"ok": True, "policy": POLICY, "task_id": "T1", "project_id": "P1", "graph_id": "G1"}


def test_altered_attachment_bytes_fail_hash() -> None:
    ctx = Dispatch()
    ctx.payload = b"altered"
    with pytest.raises(ValueError, match="bytes fail"):
        factory.preflight(ctx, {})


def test_malformed_inventory_fails() -> None:
    ctx = Dispatch()
    ctx.plan["inventory"] = [{"filename": "x"}]
    ctx.task["body"] = "<!-- SOFTWARE-FACTORY-PLAN\n" + json.dumps(ctx.plan) + "\n-->"
    with pytest.raises(ValueError, match="closed object"):
        factory.preflight(ctx, {})

@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "z" * 64])
def test_malformed_or_nonlowercase_sha_fails(bad: str) -> None:
    ctx = Dispatch()
    ctx.plan["inventory"][0]["sha256"] = bad
    ctx.task["body"] = "<!-- SOFTWARE-FACTORY-PLAN\n" + json.dumps(ctx.plan) + "\n-->"
    with pytest.raises(ValueError, match="lowercase"):
        factory.preflight(ctx, {})


def test_cross_graph_or_wrong_policy_receipt_is_rejected_without_mutation() -> None:
    ctx = Dispatch()
    receipt = {"policy": "wrong", "task_id": "T1", "project_id": "P1", "graph_id": "other", "candidate_sha": "a" * 64}
    with pytest.raises(ValueError, match="wrong policy"):
        factory.validate(ctx, {"receipt": receipt})
    assert not any(name == "kanban_create" for name, _ in ctx.calls)


def test_tammy_validation_never_dispatches_mutation() -> None:
    ctx = Dispatch()
    receipt = {"policy": POLICY, "task_id": "T1", "project_id": "P1", "graph_id": "G1", "candidate_sha": "a" * 64}
    assert factory.validate(ctx, {"receipt": receipt})["valid"]
    assert {name for name, _ in ctx.calls} <= {"kanban_show", "kanban_attachments"}


def test_materialization_is_idempotent_and_exactly_read_back() -> None:
    ctx = Dispatch()
    first = factory.materialize(ctx, {})
    second = factory.materialize(ctx, {})
    assert first == {"ok": True, "task_id": "C1", "idempotent": False}
    assert second == {"ok": True, "task_id": "C1", "idempotent": True}
    assert [name for name, _ in ctx.calls].count("kanban_create") == 1


def test_raw_create_guard_preserves_only_verified_plan_body() -> None:
    assert factory._raw_create_guard("kanban_create", {"body": "ordinary"})["action"] == "block"
    assert factory._raw_create_guard("kanban_create", {"body": "<!-- SOFTWARE-FACTORY-VERIFIED-PLAN\nG\n-->"}) is None


def test_registration_is_profile_scoped_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Registry:
        def __init__(self) -> None:
            self.tools: list[dict] = []
            self.hooks = {}

        def register_tool(self, **kw):
            self.tools.append(kw)

        def register_hook(self, name, hook):
            self.hooks[name] = hook

    monkeypatch.setenv("HERMES_PROFILE_NAME", "tammy")
    registry = Registry()
    factory.register(registry)
    assert {x["name"] for x in registry.tools} == {"software_factory_validate_candidate_receipt", "software_factory_read_receipt"}
    assert all(x["toolset"] == "software-factory" and x["schema"]["parameters"]["additionalProperties"] is False for x in registry.tools)


def test_portable_package_has_no_profile_or_home_paths() -> None:
    package = Path(factory.__file__).resolve().parent
    assert package.name == "software_factory" and package.parent.name == "src"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "/Users/" not in source and "profiles/" not in source and "subprocess" not in source
