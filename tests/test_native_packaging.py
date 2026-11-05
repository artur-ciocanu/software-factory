"""Packaging invariants plus an opt-in probe against an installed Hermes checkout."""
from __future__ import annotations

import ast
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_native_entry_point_is_static_and_colocated() -> None:
    tree = ast.parse((ROOT / "__init__.py").read_text())
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert len(imports) == 1
    assert imports[0].level == 1
    assert imports[0].module == "src.software_factory"
    assert [alias.name for alias in imports[0].names] == ["register"]
    assert (ROOT / "src/software_factory/__init__.py").is_file()
    assert (ROOT / "plugin.yaml").is_file()
    assert not any(isinstance(node, (ast.Import, ast.Call)) for node in ast.walk(tree))


def test_directory_plugin_cannot_be_a_duplicate_buildable_or_pip_identity() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert document["project"]["name"] == "software-factory"
    assert document["project"]["dependencies"] == []
    assert "entry-points" not in document["project"]
    assert "build-system" not in document
    assert document["tool"]["uv"]["package"] is False


def test_native_hermes_runtime(tmp_path: Path) -> None:
    python = os.environ.get("HERMES_TEST_PYTHON")
    source = os.environ.get("HERMES_TEST_SOURCE")
    if not python or not source:
        pytest.skip("set HERMES_TEST_PYTHON and HERMES_TEST_SOURCE for native Hermes integration")
    env = {
        **os.environ,
        "HOME": str(tmp_path / "user"),
        "HERMES_HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(filter(None, (source, os.environ.get("PYTHONPATH")))),
    }
    result = subprocess.run([python, str(ROOT / "tests/hermes_native_probe.py")],
        cwd=tmp_path, env=env, text=True, capture_output=True, check=False, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
