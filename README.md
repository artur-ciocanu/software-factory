# software-factory

A pip-entrypoint Hermes plugin for a fail-closed v2 Kanban evidence flow.

- Quentin can preflight only `handoff.json`, `sealed-plan.md`, and `caller-inventory.json` attached to the active source task. Their bytes are read only after each `stored_path` is contained beneath the documented Kanban attachment root and active task directory.
- Sheila only materializes a root whose body is an exact `SOFTWARE-FACTORY-VERIFIED-PLAN` marker bound to those same three attachments. It creates Coddy and Tammy tasks with native `kanban_create` fields, deterministic idempotency keys, and exact `kanban_show` readback.
- Coddy derives a full lowercase Git `HEAD` SHA from the persisted task worktree using a fixed argv-only Git invocation, then attaches one canonical `candidate-receipt.json` and reads it back exactly.
- Tammy uses only `kanban_show` and `kanban_attachments` plus validated attachment bytes. It verifies the receipt's canonical identity, task/project/graph binding, and independent-verifier policy.

Handlers parse the JSON strings returned by `PluginContext.dispatch_tool`; they do not assume dict returns or unsupported attachment byte options. No handler accepts a model-supplied filesystem path or native tool name.

Role-scoped registration is exact: Quentin preflight/read; Sheila materialize/read; Coddy publish/read; Tammy validate/read; Mathew and Ferris register nothing. Each handler also verifies its runtime profile.

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ty check src
uv build
```

The package needs Hermes' native Kanban attachment metadata (`id`, `filename`, `size`, `stored_path`) and a `kanban_show` task envelope containing `project_id`. If a host omits either, operations fail closed rather than infer identity.
