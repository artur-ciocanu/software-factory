# software-factory

A pip-entrypoint Hermes plugin for a narrow, fail-closed Kanban evidence flow.

```bash
mise install
uv sync --dev
uv run python -m pytest
uv run ruff check .
uv run ty check src
uv build
mise exec gitleaks -- gitleaks git --staged --redact --no-banner
```

The plugin has no filesystem or subprocess capability. It uses only fixed native Kanban dispatch names and registers role-specific tools in the `software-factory` toolset.
