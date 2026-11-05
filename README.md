# software-factory

A native Hermes directory plugin for a fail-closed v2 Kanban evidence flow.

- Quentin can preflight and publish exactly one verified root from only `handoff.json`, `sealed-plan.md`, and `caller-inventory.json` attached to the active source task. Their bytes are read only after each `stored_path` is contained beneath the documented Kanban attachment root and active task directory.
- `software_factory_publish_verified_root` is Quentin's sole root-creation path. It first validates those three source attachments, then uses fixed native `kanban_create` fields to create or reuse a Sheila-assigned root in the CLI-verified project, parented to the source task. Its canonical body is only the `SOFTWARE-FACTORY-VERIFIED-PLAN` marker with `schema_version`, `handoff_identity`, and `graph_identity`. It attaches byte-identical immutable copies of all three inputs with fixed native `kanban_attach` calls and returns durable exact readback. Its idempotency key binds source task, CLI project, handoff identity, and graph identity; a retry reuses a root only when its root fields and all attachment bytes pass exact readback, never repairing a partial or mismatched root.
- Sheila only materializes a root whose body is that exact marker bound to those same three attachments. It creates every verified Coddy, Tammy, and Ferris topology unit with native `kanban_create` fields, deterministic idempotency keys, and exact `kanban_show` readback. Ferris integration units can fan in from prior topology units but Ferris has no software-factory tools.
- Coddy derives a full lowercase Git `HEAD` SHA from the persisted task worktree using a fixed argv-only Git invocation, then attaches one canonical `candidate-receipt.json` and reads it back exactly.
- Tammy uses native `kanban_show` and `kanban_attachments` plus validated attachment bytes. It verifies the receipt's canonical identity, task/project/graph binding, and independent-verifier policy.

Handlers parse the JSON strings returned by `PluginContext.dispatch_tool`; they do not assume dict returns or unsupported attachment byte options. No handler accepts a model-supplied filesystem path or native tool name.

Role-scoped registration is exact: Quentin preflight/publish-root/read; Sheila materialize/read; Coddy publish-candidate/read; Tammy validate-candidate/read; Mathew and Ferris register nothing. Quentin's raw `kanban_create` route is blocked in favor of the publication tool, and each handler verifies its runtime profile.

## Clean-profile installation

Install the reviewed directory through Hermes, not a wheel into the current Python environment. Manual `pip` installs are not durable declarations: a later Hermes PM environment replacement need not retain them. The root `plugin.yaml` supplies discovery identity; root `__init__.py` statically imports the colocated `src/software_factory` package with a relative import. No installed `software_factory` distribution, path mutation, or import shim is needed.

The project is intentionally virtual (`tool.uv.package = false`, no build backend or pip entry point), not a wheel distribution. Four buildable profile-local copies named `software-factory` would collide in Hermes' shared workspace. Current PM gives virtual members distinct path-derived names and strips their development dependency groups during runtime preparation. Runtime dependencies remain empty. Keep the entire plugin directory intact, including `src/`; do not install it from a profile skill tree or sibling profile directory.

After review and publication of the pinned commit, run the following **only** for `quentin`, `sheila`, `coddy`, and `tammy` (the pin is the packaging/test implementation commit; later documentation-only commits record it and clarify rollout). These commands are an operator-approved activation/migration, **not** an unconditional staging-only procedure: `--no-enable` prevents a new enablement but does not disable an existing selection. Installation/replacement and enablement can notify running backends/gateways to load the plugin, making hooks live before a new chat starts. For staging-only validation, use an isolated home/runtime rather than an already-selected production profile.

```bash
SF_COMMIT=cb92461ec1774832fd0c3b7f5575cf1cbf088daa
for profile in quentin sheila coddy tammy; do
  hermes -p "$profile" plugins install \
    https://github.com/artur-ciocanu/software-factory.git \
    --ref "$SF_COMMIT" --no-enable
  hermes -p "$profile" plugins enable software-factory --no-allow-tool-override
  hermes -p "$profile" plugins list
  hermes -p "$profile" config get platform_toolsets.cli
done
```

Using the existing toolset list returned above, append only `software-factory` with the supported config command for each authorized profile:

```bash
hermes -p <profile> config set platform_toolsets.cli '<existing-toolsets-plus-software-factory>'
```

Do not install or enable this plugin for Mathew or Ferris. Preserve every profile's existing restrictions. In particular, preserve Sheila's native allowlist and add only `software-factory`; **do not add terminal, file, or code_execution**. This packaging change grants no tool overrides or new permissions. Read Hermes' activation report after each operation: hooks may be live immediately, while Python tools are deferred to a fresh session. Do not assume a gateway restart is required or that avoiding a restart prevents activation. Any separately needed gateway restart requires its own operator approval.

Older wheel-only installations are not the supported deployment. A same-name directory identity shadows a pip entry point in current Hermes discovery, but this tree declares no pip entry point and does not rely on one. Inspect migration state with `plugins list`; do not manually inject the old wheel into a replacement PM environment.

## Verification

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ty check src
```

The default suite checks packaging invariants and existing handler contracts. Its native integration test explicitly skips unless both `HERMES_TEST_PYTHON` (Hermes runtime interpreter) and `HERMES_TEST_SOURCE` (current Hermes checkout) are set. To exercise the actual Hermes scanner, loader, context, registries, Plugin Doctor, and PM union/preparation APIs:

```bash
# Choose an isolated scratch directory, outside the plugin checkout.
SCRATCH=<isolated-scratch-directory>
mkdir -p "$SCRATCH"
# If Hermes' interpreter lacks tomli_w, supply it only to this test process.
uv pip install --target "$SCRATCH/test-deps" tomli-w
HERMES_TEST_PYTHON=<hermes-runtime-python> \
HERMES_TEST_SOURCE=<hermes-source-checkout> \
PYTHONPATH="$SCRATCH/test-deps" \
PYTHONPYCACHEPREFIX="$SCRATCH/pycache" \
TMPDIR="$SCRATCH" \
uv run pytest --basetemp="$SCRATCH/pytest"
```

The executable probe creates temporary home/profile trees and directory copies, verifies exact tool and guard-hook registration for all four authorized roles and zero registration for Mathew/Ferris, and invokes registered handlers and guard hooks without touching real Kanban data. It changes `HOME` as well as `HERMES_HOME`: a scratch directory nested under the real `~/.hermes` otherwise still resolves to the real shared PM root. The test asserts the isolated dependency root before reading the four-profile union.

The union check uses actual PM virtual-member preparation, verifies four unique project names, then runs offline `uv sync --all-packages` into two fresh environments (deleting the first) and imports every directory's root entry point without an installed `software_factory` package. This is a bounded dependency-free workspace fixture, **not** a full Hermes environment publication/replacement test. No production profiles, gateway lifecycle, live Kanban queues, or Hermes source files are modified. Plugin Doctor's temporary default/custom profile may report warnings for declared tools/hooks that role-scoped registration intentionally omits; native per-profile assertions are the authority for role coverage.

Runtime contracts were checked against the current local Hermes sources: `website/docs/developer-guide/plugins/index.md`, `hermes_cli/plugins_loader.py`, `hermes_cli/plugins.py`, `pm/plugin_declarations.py`, and `pm/workspace.py`. See the current [official plugin documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins).

The package needs Hermes' native Kanban attachment metadata (`id`, `filename`, `size`, `stored_path`). Hermes' native `kanban_show` currently omits `task.project_id`, so the plugin preserves that native read and makes one fixed public CLI read, `hermes kanban show <active-task-id> --json`, solely to obtain `project_id`. The invocation is argv-only (no shell and no model-controlled executable or arguments); it requires the CLI task ID and all shared task fields to agree with the native read, then accepts only a nonempty CLI `project_id`. A failed command, malformed JSON, mismatched task, or missing project ID fails closed. This is a compatibility limitation: the plugin depends on the installed public Hermes CLI continuing to expose `project_id` in its JSON task output.
