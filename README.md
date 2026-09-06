# software-factory

A pip-entrypoint Hermes plugin for a fail-closed v2 Kanban evidence flow.

- Quentin can preflight and publish exactly one verified root from only `handoff.json`, `sealed-plan.md`, and `caller-inventory.json` attached to the active source task. Their bytes are read only after each `stored_path` is contained beneath the documented Kanban attachment root and active task directory.
- `software_factory_publish_verified_root` is Quentin's sole root-creation path. It first validates those three source attachments, then uses fixed native `kanban_create` fields to create or reuse a Sheila-assigned root in the CLI-verified project, parented to the source task. Its canonical body is only the `SOFTWARE-FACTORY-VERIFIED-PLAN` marker with `schema_version`, `handoff_identity`, and `graph_identity`. It attaches byte-identical immutable copies of all three inputs with fixed native `kanban_attach` calls and returns durable exact readback. Its idempotency key binds source task, CLI project, handoff identity, and graph identity; a retry reuses a root only when its root fields and all attachment bytes pass exact readback, never repairing a partial or mismatched root.
- Sheila only materializes a root whose body is that exact marker bound to those same three attachments. It creates every verified Coddy, Tammy, and Ferris topology unit with native `kanban_create` fields, deterministic idempotency keys, and exact `kanban_show` readback. Ferris integration units can fan in from prior topology units but Ferris has no software-factory tools.
- Coddy derives a full lowercase Git `HEAD` SHA from the persisted task worktree using a fixed argv-only Git invocation, then attaches one canonical `candidate-receipt.json` and reads it back exactly.
- Tammy uses native `kanban_show` and `kanban_attachments` plus validated attachment bytes. It verifies the receipt's canonical identity, task/project/graph binding, and independent-verifier policy.

Handlers parse the JSON strings returned by `PluginContext.dispatch_tool`; they do not assume dict returns or unsupported attachment byte options. No handler accepts a model-supplied filesystem path or native tool name.

Role-scoped registration is exact: Quentin preflight/publish-root/read; Sheila materialize/read; Coddy publish-candidate/read; Tammy validate-candidate/read; Mathew and Ferris register nothing. Quentin's raw `kanban_create` route is blocked in favor of the publication tool, and each handler verifies its runtime profile.

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ty check src
uv build
```

The package needs Hermes' native Kanban attachment metadata (`id`, `filename`, `size`, `stored_path`). Hermes' native `kanban_show` currently omits `task.project_id`, so the plugin preserves that native read and makes one fixed public CLI read, `hermes kanban show <active-task-id> --json`, solely to obtain `project_id`. The invocation is argv-only (no shell and no model-controlled executable or arguments); it requires the CLI task ID and all shared task fields to agree with the native read, then accepts only a nonempty CLI `project_id`. A failed command, malformed JSON, mismatched task, or missing project ID fails closed. This is a compatibility limitation: the plugin depends on the installed public Hermes CLI continuing to expose `project_id` in its JSON task output.
