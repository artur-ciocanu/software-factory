"""Closed, portable validation for the software-factory v2 wire contracts."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

SHA40 = re.compile(r"\A[0-9a-f]{40}\Z")
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
SLUG = re.compile(r"\A[a-z][a-z0-9_-]{1,62}\Z")
HANDOFF_FILENAMES = ("handoff.json", "sealed-plan.md", "caller-inventory.json")
ROOT_MARKER = "SOFTWARE-FACTORY-VERIFIED-PLAN"
UNIT_MARKER = "SOFTWARE-FACTORY-UNIT"
RECEIPT_FILENAME = "candidate-receipt.json"


class ContractError(ValueError):
    """Input is not an exact software-factory contract value."""


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: object) -> str:
    return sha256(canonical_json(value)).hexdigest()


def graph_identity(value: object) -> str:
    """Return a handoff identity without trusting its self-referential claim."""
    raw = _object(value, "verified_handoff")
    plan = _object(raw.get("verified_plan"), "verified plan")
    supersession = _object(plan.get("supersession"), "supersession")
    return digest(
        {
            **raw,
            "verified_plan": {
                **plan,
                "supersession": {key: item for key, item in supersession.items() if key != "graph_identity"},
            },
        }
    )


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} must be an object")
    return value


def _only(raw: dict[str, Any], fields: set[str], where: str) -> None:
    if set(raw) != fields:
        raise ContractError(f"{where} keys mismatch")


def _text(value: object, where: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where} must be a non-empty string")
    if pattern and not pattern.fullmatch(value):
        raise ContractError(f"{where} has invalid format")
    if value.startswith("p_"):
        raise ContractError(f"{where} must not contain a profile-local project id")
    return value


def _array(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractError(f"{where} must be an array")
    return value


@dataclass(frozen=True)
class CallerDisposition:
    caller: str
    kind: str
    disposition: str
    owning_execution_unit: str

    @classmethod
    def parse(cls, value: object) -> CallerDisposition:
        raw = _object(value, "caller_manifest.entries[]")
        _only(raw, {"caller", "kind", "disposition", "owning_execution_unit"}, "caller entry")
        kind = _text(raw["kind"], "caller kind")
        disposition = _text(raw["disposition"], "caller disposition")
        if kind not in {"production", "executable-test"} or disposition not in {"removed", "replaced", "retained"}:
            raise ContractError("unsupported caller disposition")
        return cls(_text(raw["caller"], "caller"), kind, disposition, _text(raw["owning_execution_unit"], "owning unit"))


@dataclass(frozen=True)
class CallerManifest:
    inventory_sha256: str
    entries: tuple[CallerDisposition, ...]

    @classmethod
    def parse(cls, value: object) -> CallerManifest:
        raw = _object(value, "caller_manifest")
        _only(raw, {"inventory_sha256", "declared_complete", "entries"}, "caller_manifest")
        if raw["declared_complete"] is not True:
            raise ContractError("caller manifest must declare complete")
        entries = tuple(CallerDisposition.parse(x) for x in _array(raw["entries"], "caller entries"))
        if not entries or len({(x.caller, x.kind) for x in entries}) != len(entries):
            raise ContractError("caller entries must be non-empty and unique")
        return cls(_text(raw["inventory_sha256"], "inventory_sha256", SHA256), entries)


@dataclass(frozen=True)
class Unit:
    unit_id: str
    owner: str
    parents: tuple[str, ...]

    @classmethod
    def parse(cls, value: object) -> Unit:
        raw = _object(value, "topology unit")
        _only(raw, {"unit_id", "owner", "phases", "parents"}, "topology unit")
        owner = _text(raw["owner"], "unit owner")
        if owner not in {"coddy", "tammy", "ferris"}:
            raise ContractError("unknown execution owner")
        phases = _array(raw["phases"], "unit phases")
        if not phases:
            raise ContractError("unit phases must be non-empty")
        for phase in phases:
            p = _object(phase, "phase")
            _only(p, {"name", "shares_card", "shares_worktree", "shares_branch", "shares_candidate", "shares_verifier"}, "phase")
            if not isinstance(p["name"], str) or any(type(p[k]) is not bool for k in set(p) - {"name"}):
                raise ContractError("phase fields are invalid")
            if p["shares_candidate"] and not p["shares_branch"]:
                raise ContractError("shared candidate requires shared branch")
        return cls(_text(raw["unit_id"], "unit id"), owner, tuple(_text(x, "unit parent") for x in _array(raw["parents"], "unit parents")))


@dataclass(frozen=True)
class CandidatePolicy:
    implementation_unit: str
    verifier_unit: str

    @classmethod
    def parse(cls, value: object) -> CandidatePolicy:
        raw = _object(value, "candidate policy")
        _only(raw, {"implementation_unit", "verifier_unit", "verifier"}, "candidate policy")
        if raw["verifier"] != "tammy":
            raise ContractError("candidate policy requires tammy")
        return cls(_text(raw["implementation_unit"], "implementation unit"), _text(raw["verifier_unit"], "verifier unit"))


@dataclass(frozen=True)
class VerifiedHandoff:
    plan_id: str
    logical_project_slug: str
    implementation_base_sha: str
    sealed_plan_sha256: str
    sealed_plan_size: int
    caller_manifest: CallerManifest
    units: tuple[Unit, ...]
    candidate_policy: tuple[CandidatePolicy, ...]
    graph_identity: str

    @classmethod
    def parse(cls, value: object) -> VerifiedHandoff:
        raw = _object(value, "verified_handoff")
        _only(raw, {"schema_version", "requested_by", "board_slug", "verified_plan", "capability_requirements"}, "verified_handoff")
        if raw["schema_version"] != 2 or raw["requested_by"] != "quentin":
            raise ContractError("handoff must be v2 and requested by quentin")
        if not SLUG.fullmatch(_text(raw["board_slug"], "board slug")):
            raise ContractError("board slug has invalid format")
        requirements = _array(raw["capability_requirements"], "capability requirements")
        profiles: set[str] = set()
        for requirement in requirements:
            r = _object(requirement, "capability requirement")
            _only(r, {"profile", "capabilities"}, "capability requirement")
            profile = _text(r["profile"], "capability profile")
            caps = _array(r["capabilities"], "capabilities")
            if profile not in {"coddy", "tammy", "ferris"} or not caps or not all(isinstance(x, str) and x for x in caps):
                raise ContractError("invalid capability requirement")
            profiles.add(profile)
        if profiles != {"coddy", "tammy", "ferris"}:
            raise ContractError("capabilities must cover execution profiles")
        plan = _object(raw["verified_plan"], "verified plan")
        fields = {"plan_id", "logical_project_slug", "implementation_base_sha", "sealed_plan_sha256", "sealed_plan_size", "caller_manifest", "topology", "candidate_policy", "supersession"}
        _only(plan, fields, "verified plan")
        size = plan["sealed_plan_size"]
        if type(size) is not int or size < 1:
            raise ContractError("sealed plan size is invalid")
        units = tuple(Unit.parse(x) for x in _array(plan["topology"], "topology"))
        by_id = {x.unit_id: x for x in units}
        if not units or len(by_id) != len(units) or any(p not in by_id or p == x.unit_id for x in units for p in x.parents):
            raise ContractError("topology graph is invalid")
        children = {unit_id: set() for unit_id in by_id}
        for unit in units:
            for parent in unit.parents:
                children[parent].add(unit.unit_id)
        pending = {unit_id: len(unit.parents) for unit_id, unit in by_id.items()}
        ready = [unit_id for unit_id, count in pending.items() if count == 0]
        visited = 0
        while ready:
            unit_id = ready.pop()
            visited += 1
            for child in children[unit_id]:
                pending[child] -= 1
                if pending[child] == 0:
                    ready.append(child)
        if visited != len(by_id):
            raise ContractError("topology graph contains a cycle")
        policy = tuple(CandidatePolicy.parse(x) for x in _array(plan["candidate_policy"], "candidate policy"))
        if not policy or len({(x.implementation_unit, x.verifier_unit) for x in policy}) != len(policy):
            raise ContractError("candidate policy is invalid")
        for p in policy:
            if p.implementation_unit not in by_id or p.verifier_unit not in by_id or by_id[p.implementation_unit].owner != "coddy" or by_id[p.verifier_unit].owner != "tammy" or p.implementation_unit not in by_id[p.verifier_unit].parents:
                raise ContractError("candidate policy does not bind independent verifier")
        terminals = [unit for unit in units if not children[unit.unit_id]]
        if len(terminals) != 1 or terminals[0].owner != "ferris":
            raise ContractError("topology requires exactly one ferris terminal")
        if not {p.verifier_unit for p in policy} <= set(terminals[0].parents):
            raise ContractError("ferris terminal must depend on every selected tammy verifier")
        sup = _object(plan["supersession"], "supersession")
        _only(sup, {"graph_identity", "predecessor_graph_identity", "recovery_attempt"}, "supersession")
        graph = _text(sup["graph_identity"], "graph identity", SHA256)
        predecessor = sup["predecessor_graph_identity"]
        attempt = sup["recovery_attempt"]
        if (predecessor is not None and not isinstance(predecessor, str)) or type(attempt) is not int or attempt < 0 or ((attempt == 0) != (predecessor is None)):
            raise ContractError("supersession is invalid")
        if graph != graph_identity(raw):
            raise ContractError("declared graph identity does not match canonical handoff")
        return cls(_text(plan["plan_id"], "plan id", SLUG), _text(plan["logical_project_slug"], "project slug", SLUG), _text(plan["implementation_base_sha"], "base sha", SHA40), _text(plan["sealed_plan_sha256"], "sealed plan sha", SHA256), size, CallerManifest.parse(plan["caller_manifest"]), units, policy, graph)

    def identity(self) -> str:
        return self.graph_identity


def validate_evidence(handoff: VerifiedHandoff, sealed_plan: bytes, caller_inventory: bytes) -> None:
    if len(sealed_plan) != handoff.sealed_plan_size or sha256(sealed_plan).hexdigest() != handoff.sealed_plan_sha256:
        raise ContractError("sealed plan bytes do not match handoff proof")
    if sha256(caller_inventory).hexdigest() != handoff.caller_manifest.inventory_sha256:
        raise ContractError("caller inventory bytes do not match manifest")
    try:
        inventory = json.loads(caller_inventory)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("caller inventory is invalid JSON") from exc
    raw = _object(inventory, "caller inventory")
    _only(raw, {"schema_version", "active_callers"}, "caller inventory")
    if raw["schema_version"] != 1:
        raise ContractError("caller inventory schema is unsupported")
    actual = set()
    for item in _array(raw["active_callers"], "active callers"):
        row = _object(item, "active caller")
        _only(row, {"caller", "kind"}, "active caller")
        actual.add((_text(row["caller"], "active caller"), _text(row["kind"], "active caller kind")))
    expected = {(x.caller, x.kind) for x in handoff.caller_manifest.entries}
    if not actual or actual != expected:
        raise ContractError("caller manifest is not an exact inventory disposition")


def parse_candidate_receipt(value: object) -> dict[str, str]:
    raw = _object(value, "candidate receipt")
    _only(raw, {"schema_version", "handoff_identity", "implementation_unit", "implementation_task_id", "candidate_sha", "candidate_receipt_id"}, "candidate receipt")
    if raw["schema_version"] != 1:
        raise ContractError("candidate receipt schema is unsupported")
    receipt = {
        key: _text(
            raw[key],
            key,
            SHA40 if key == "candidate_sha" else SHA256 if key in {"handoff_identity", "candidate_receipt_id"} else None,
        )
        for key in raw
        if key != "schema_version"
    }
    expected = digest({"schema_version": 1, **{k: receipt[k] for k in ("handoff_identity", "implementation_unit", "implementation_task_id", "candidate_sha")}})
    if receipt["candidate_receipt_id"] != expected:
        raise ContractError("candidate receipt identity mismatch")
    return receipt
