"""Executable integration probes; run with Hermes' Python and source on PYTHONPATH.

Only temporary homes/copies are touched. No native Kanban operation is dispatched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cli.plugin_dev import doctor_plugin
from hermes_cli.plugins import PluginManager
from pm import workspace
from pm.environments import dependency_home_root
from pm.plugin_declarations import read_python_declaration
from tools.registry import registry

REPO = Path(__file__).resolve().parents[1]
READ = "software_factory_read_receipt"
EXPECTED = {
    "quentin": {READ, "software_factory_preflight_verified_plan", "software_factory_publish_verified_root"},
    "sheila": {READ, "software_factory_materialize_verified_plan"},
    "coddy": {READ, "software_factory_publish_candidate_receipt"},
    "tammy": {READ, "software_factory_validate_candidate_receipt"},
    "mathew": set(),
    "ferris": set(),
}
IGNORE = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist")


class NativePackaging(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="software-factory-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {
            "HOME": str(self.root / "user"),
            "HERMES_HOME": str(self.root / "home"),
            "HERMES_ENABLE_PROJECT_PLUGINS": "0",
            "HERMES_CONFIG_BACKEND": "file",
        }).start()
        for name in ("HERMES_PROFILE", "HERMES_PROFILE_NAME", "HERMES_KANBAN_TASK"):
            os.environ.pop(name, None)

    def test_native_loader_roles_hooks_and_bound_handlers(self):
        for role, expected in EXPECTED.items():
            profile = self.root / "home" / "profiles" / role
            plugin = profile / "plugins" / "software-factory"
            shutil.copytree(REPO, plugin, ignore=IGNORE)
            (profile / "config.yaml").write_text("plugins:\n  enabled: [software-factory]\n", encoding="utf-8")
            with self.subTest(role=role), patch.dict(os.environ, {"HERMES_HOME": str(profile)}):
                manager = PluginManager()
                try:
                    manifests = manager._scan_directory(profile / "plugins", source="user")
                    self.assertEqual(len(manifests), 1)
                    manager._load_plugin(manifests[0])
                    loaded = manager._plugins["software-factory"]
                    self.assertTrue(loaded.enabled, loaded.error)
                    self.assertEqual(set(loaded.tools_registered), expected)
                    self.assertEqual(set(loaded.hooks_registered), {"pre_tool_call"} if role in {"quentin", "sheila"} else set())
                    runtime = sys.modules[loaded.module.register.__module__]
                    self.assertTrue(Path(runtime.__file__).is_relative_to(plugin))
                    # No unqualified installed wheel may provide the native implementation.
                    self.assertTrue(runtime.__name__.startswith("hermes_plugins."))
                    with patch.object(runtime, "_dispatch", side_effect=AssertionError("native dispatch forbidden")):
                        # Invoke every actual registered executable without Hermes' error
                        # formatter importing unrelated built-ins on expected failures.
                        for name in expected:
                            entry = registry.get_entry(name)
                            self.assertIsNotNone(entry)
                            with self.assertRaisesRegex(ValueError, "active Kanban task"):
                                entry.handler({})
                            with patch.object(runtime, "_profile", return_value="mathew"), self.assertRaisesRegex(ValueError, "runtime profile"):
                                entry.handler({})
                        if role == "quentin":
                            result = manager.invoke_hook("pre_tool_call", tool_name="kanban_create", args={})
                            self.assertEqual(result[0]["action"], "block")
                        if role == "sheila":
                            marker = {"schema_version": 1, "handoff_identity": "a" * 64, "graph_identity": "b" * 64}
                            task = {"body": "<!-- SOFTWARE-FACTORY-VERIFIED-PLAN\n" + json.dumps(marker) + "\n-->"}
                            with patch.dict(os.environ, {"HERMES_KANBAN_TASK": "scratch-only"}), patch.object(runtime, "_show", return_value=(task, {})):
                                result = manager.invoke_hook("pre_tool_call", tool_name="kanban_create", args={})
                                self.assertEqual(result[0]["action"], "block")
                finally:
                    manager.unload()
        report = doctor_plugin(REPO)
        self.assertFalse([finding for finding in report.findings if finding.level == "error"])

    def test_four_profile_union_and_fresh_environments(self):
        home = self.root / "home"
        home.mkdir()
        selected = []
        for role in ("quentin", "sheila", "coddy", "tammy"):
            profile = home / "profiles" / role
            plugin = profile / "plugins" / "software-factory"
            shutil.copytree(REPO, plugin, ignore=IGNORE)
            (profile / "config.yaml").write_text("plugins:\n  enabled: [software-factory]\n", encoding="utf-8")
            selected.append(plugin)
        self.assertEqual(dependency_home_root(), home)
        self.assertEqual(set(workspace.enabled_plugin_dirs()), set(selected))
        self.assertEqual(set(workspace.enabled_member_dirs()), set(selected))
        for plugin in selected:
            declaration = read_python_declaration(plugin)
            self.assertEqual(declaration.requirements, ())
            self.assertTrue(declaration.installs_packages)  # dev groups select virtual members
        # Actual PM preparation, not a parallel reimplementation of renaming/pruning.
        root = self.root / "workspace"
        members = [workspace._workspace_member(plugin, root, identity=plugin) for plugin in selected]
        metadata = [tomllib.loads((member / "pyproject.toml").read_text()) for member in members]
        names = [document["project"]["name"] for document in metadata]
        self.assertEqual(len(set(names)), 4)
        for document in metadata:
            self.assertNotIn("build-system", document)
            self.assertNotIn("dependency-groups", document)
            self.assertFalse(document["tool"]["uv"]["package"])
            self.assertNotIn("entry-points", document["project"])
        # Minimal dependency-free core fixture bounds this to packaging; no full Hermes solve.
        relative = [member.relative_to(root).as_posix() for member in members]
        (root / "pyproject.toml").write_text(
            '[project]\nname = "packaging-probe"\nversion = "0.0.0"\nrequires-python = ">=3.11"\ndependencies = []\n'
            '[tool.uv]\npackage = false\n[tool.uv.workspace]\nmembers = ' + json.dumps(relative) + '\n', encoding="utf-8")
        for generation in ("first", "replacement"):
            environment = self.root / generation
            result = subprocess.run(
                ["uv", "sync", "--offline", "--no-dev", "--all-packages", "--python", sys.executable],
                cwd=root, env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            # Fresh interpreters can import each copy without any installed software_factory.
            for plugin in selected:
                code = (
                    "try:\n    import software_factory\n"
                    "except ModuleNotFoundError:\n    pass\n"
                    "else:\n    raise AssertionError('unexpected installed software_factory')\n"
                    "from software_factory_probe import register\n"
                    "from software_factory_probe.src.software_factory import ROLES\n"
                    "assert register.__module__ == 'software_factory_probe.src.software_factory'\n"
                    "assert set(ROLES) == {'quentin', 'sheila', 'coddy', 'tammy'}\n"
                )
                alias = self.root / "import-root" / "software_factory_probe"
                alias.parent.mkdir(exist_ok=True)
                if alias.is_symlink():
                    alias.unlink()
                alias.symlink_to(plugin, target_is_directory=True)
                # PYTHONPATH is process configuration, never mutated by plugin code.
                result = subprocess.run([str(environment / "bin" / "python"), "-c", code], cwd=self.root,
                    env={**os.environ, "PYTHONPATH": str(alias.parent)}, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
            if generation == "first":
                shutil.rmtree(environment)


if __name__ == "__main__":
    unittest.main(verbosity=2)
