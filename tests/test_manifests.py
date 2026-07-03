"""Tests for plugin/marketplace manifest files (Claude + Codex).

Validates: all manifest JSON files parse; the two plugin manifests
(.claude-plugin/plugin.json, .codex-plugin/plugin.json) stay in lockstep on
version; the Codex plugin manifest has the required fields with correct
shapes; the Codex marketplace manifest points at this repo via a
`./`-prefixed local source path.
"""
import json
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

CLAUDE_PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_PLUGIN_MANIFEST = REPO_ROOT / ".codex-plugin" / "plugin.json"
CODEX_MARKETPLACE_MANIFEST = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"

HOOKS_DIR = REPO_ROOT / "hooks"
CLAUDE_HOOKS_MANIFEST = HOOKS_DIR / "hooks.json"
CODEX_HOOKS_MANIFEST = HOOKS_DIR / "codex-hooks.json"

KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ManifestJsonValidityTests(unittest.TestCase):
    def test_claude_plugin_manifest_parses_as_json(self):
        self.assertIsInstance(_load_json(CLAUDE_PLUGIN_MANIFEST), dict)

    def test_claude_marketplace_manifest_parses_as_json(self):
        self.assertIsInstance(_load_json(CLAUDE_MARKETPLACE_MANIFEST), dict)

    def test_codex_plugin_manifest_parses_as_json(self):
        self.assertIsInstance(_load_json(CODEX_PLUGIN_MANIFEST), dict)

    def test_codex_marketplace_manifest_parses_as_json(self):
        self.assertIsInstance(_load_json(CODEX_MARKETPLACE_MANIFEST), dict)


class PluginVersionLockstepTests(unittest.TestCase):
    def test_plugin_versions_match_across_claude_and_codex(self):
        claude = _load_json(CLAUDE_PLUGIN_MANIFEST)
        codex = _load_json(CODEX_PLUGIN_MANIFEST)
        self.assertEqual(claude["version"], codex["version"])


class CodexPluginManifestShapeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = _load_json(CODEX_PLUGIN_MANIFEST)

    def test_has_required_fields(self):
        for field in ("name", "version", "description"):
            self.assertIn(field, self.manifest)

    def test_name_is_kebab_case(self):
        self.assertRegex(self.manifest["name"], KEBAB_CASE_RE)

    def test_version_is_semver(self):
        self.assertRegex(self.manifest["version"], SEMVER_RE)

    def test_description_matches_claude_plugin_manifest(self):
        claude = _load_json(CLAUDE_PLUGIN_MANIFEST)
        self.assertEqual(self.manifest["description"], claude["description"])


class CodexMarketplaceManifestShapeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = _load_json(CODEX_MARKETPLACE_MANIFEST)

    def test_has_name_and_display_name(self):
        self.assertIn("name", self.manifest)
        self.assertIn("interface", self.manifest)
        self.assertIn("displayName", self.manifest["interface"])

    def test_has_plugins_list_with_local_source(self):
        self.assertIn("plugins", self.manifest)
        self.assertTrue(len(self.manifest["plugins"]) >= 1)
        plugin = self.manifest["plugins"][0]
        self.assertEqual(plugin["name"], "theforge")
        self.assertIn("source", plugin)
        self.assertEqual(plugin["source"]["source"], "local")

    def test_source_path_is_dot_slash_prefixed(self):
        plugin = self.manifest["plugins"][0]
        self.assertTrue(plugin["source"]["path"].startswith("./"))


def _session_start_commands(hook_config):
    """Flatten every command string wired to SessionStart in a hooks.json dict."""
    commands = []
    for matcher_group in hook_config.get("hooks", {}).get("SessionStart", []):
        for hook in matcher_group.get("hooks", []):
            if "command" in hook:
                commands.append(hook["command"])
    return commands


class HookConfigTests(unittest.TestCase):
    """Codex hook-wiring verification (Task 3, 2026-07-03).

    Fetched https://developers.openai.com/codex/hooks and
    https://developers.openai.com/codex/plugins/build. Findings:

    - Codex's plugin hooks.json schema is structurally identical to Claude's:
      `{"hooks": {"<Event>": [{"matcher": ..., "hooks": [{"type": "command",
      "command": ...}]}]}}`. Codex additionally accepts (but does not yet
      execute) an `async` handler field, so the existing `"async": false`
      entries in hooks/hooks.json are inert-but-valid under Codex, not a
      schema violation.
    - Codex checks `hooks/hooks.json` as the default plugin hook file
      automatically (no `hooks` entry needed in .codex-plugin/plugin.json,
      and this repo's manifest has none).
    - Codex sets `CLAUDE_PLUGIN_ROOT` (alongside its native `PLUGIN_ROOT`)
      specifically for compatibility with existing Claude plugin hooks, so
      the `${CLAUDE_PLUGIN_ROOT}` reference already in hooks/hooks.json
      resolves correctly under Codex too.

    Outcome: COMPATIBLE. hooks/hooks.json is shared byte-identical between
    harnesses; hooks/codex-hooks.json is deliberately absent. The tests below
    assert that outcome directly rather than skipping when the collision
    artifact is missing, so a regression (or an unverified future change)
    fails loudly instead of reporting false confidence.
    """

    def test_all_hook_json_files_parse(self):
        hook_json_files = sorted(HOOKS_DIR.glob("*.json"))
        self.assertTrue(len(hook_json_files) >= 1)
        for path in hook_json_files:
            self.assertIsInstance(_load_json(path), dict)

    def test_claude_hooks_json_wires_session_start_to_session_start_script(self):
        config = _load_json(CLAUDE_HOOKS_MANIFEST)
        commands = _session_start_commands(config)
        self.assertTrue(
            any("hooks/session-start" in command for command in commands),
            f"expected a SessionStart command referencing hooks/session-start, got {commands}",
        )

    def test_claude_hooks_json_uses_plugin_root_env_var_codex_also_supports(self):
        # Codex sets CLAUDE_PLUGIN_ROOT for compatibility with existing
        # Claude plugin hooks (see class docstring), so this reference
        # resolves correctly under both harnesses without modification.
        config = _load_json(CLAUDE_HOOKS_MANIFEST)
        commands = _session_start_commands(config)
        self.assertTrue(
            any("${CLAUDE_PLUGIN_ROOT}" in command for command in commands),
            f"expected a SessionStart command referencing ${{CLAUDE_PLUGIN_ROOT}}, got {commands}",
        )

    def test_codex_hooks_json_absent_confirms_shared_schema_outcome(self):
        # Verified compatible: Codex's hooks.json schema and plugin-root env
        # var both match Claude's (see class docstring), so no separate
        # hooks/codex-hooks.json collision file is needed. If this ever
        # needs to be created, this test must be updated deliberately as
        # part of that change, not left to silently skip.
        self.assertFalse(
            CODEX_HOOKS_MANIFEST.exists(),
            "hooks/codex-hooks.json exists but the recorded verification outcome "
            "was 'compatible schema, shared hooks.json' — update this test and "
            "the HookConfigTests docstring if the collision path is now needed.",
        )

    def test_codex_hooks_json_references_same_session_start_script_if_present(self):
        # Defensive: if a future re-verification lands on the collision path
        # and hooks/codex-hooks.json is (re)introduced, it must still wire to
        # the same shared session-start script.
        if not CODEX_HOOKS_MANIFEST.exists():
            self.skipTest("hooks/codex-hooks.json not present (shared hooks.json outcome; see test above)")
        config = _load_json(CODEX_HOOKS_MANIFEST)
        commands = _session_start_commands(config)
        self.assertTrue(
            any("hooks/session-start" in command for command in commands),
            f"expected a SessionStart command referencing hooks/session-start, got {commands}",
        )


if __name__ == "__main__":
    unittest.main()
