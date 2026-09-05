"""forge_memory_store: FileStore and GitHubStore both implement create/list/
retire over the shared forge_memory schema/render/parse/validate; store
selection is config-driven for deferrals and hardwired to FileStore for
constraints; every gh failure mode is loud and names its fix, never a
silent slide to the file store."""
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import forge_memory  # noqa: E402
import forge_memory_store as fms  # noqa: E402


def _deferral(title="fix-the-thing", why="Needed later.", frm="user",
              follow_up="backlog"):
    return forge_memory.Record(
        type="deferral",
        fields={
            "title": title,
            "why": why,
            "from": frm,
            "follow-up": follow_up,
        },
    )


def _constraint(id_="no-network-calls", rule="Never call the network.",
                because="Determinism.", added="2026-09-01", source="user"):
    return forge_memory.Record(
        type="constraint",
        fields={
            "id": id_,
            "rule": rule,
            "because": because,
            "added": added,
            "source": source,
        },
    )


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-store-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "deferrals.md")

    def test_create_then_list_round_trips(self):
        store = fms.FileStore(self.path)
        ref = store.create(_deferral(title="first-item"))
        self.assertEqual(ref, "first-item")
        records = store.list("deferral")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["title"], "first-item")

    def test_file_is_canonical_after_create(self):
        store = fms.FileStore(self.path)
        store.create(_deferral(title="alpha"))
        store.create(_deferral(title="beta"))
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        records = forge_memory.parse(text, "deferral")
        expected = "\n".join(
            forge_memory.render(r).rstrip("\n") for r in records
        ) + "\n"
        self.assertEqual(text, expected)

    def test_retire_removes_record_entirely_no_tombstone(self):
        store = fms.FileStore(self.path)
        store.create(_deferral(title="alpha"))
        store.create(_deferral(title="beta"))
        store.retire("alpha")
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("alpha", text)
        records = forge_memory.parse(text, "deferral")
        self.assertEqual([r.fields["title"] for r in records], ["beta"])
        # remaining file is canonical
        expected = "\n".join(
            forge_memory.render(r).rstrip("\n") for r in records
        ) + "\n"
        self.assertEqual(text, expected)

    def test_slug_retired_can_be_readded(self):
        store = fms.FileStore(self.path)
        store.create(_deferral(title="alpha"))
        store.retire("alpha")
        ref = store.create(_deferral(title="alpha", why="Different reason."))
        self.assertEqual(ref, "alpha")
        records = store.list("deferral")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["why"], "Different reason.")

    def test_constraint_file_store_round_trip(self):
        path = os.path.join(self.tmp, "constraints.md")
        store = fms.FileStore(path)
        ref = store.create(_constraint())
        self.assertEqual(ref, "no-network-calls")
        records = store.list("constraint")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["rule"], "Never call the network.")


class SelectStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-select-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(os.path.join(self.tmp, "docs", "forge"), exist_ok=True)

    def _write_config(self, obj_or_text):
        path = os.path.join(self.tmp, "docs", "forge", "config.json")
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(obj_or_text, str):
                f.write(obj_or_text)
            else:
                json.dump(obj_or_text, f)
        return path

    def test_constraint_always_filestore_ignoring_config(self):
        self._write_config({"deferrals": {"store": "file"}})
        store = fms.select_store(self.tmp, "constraint")
        self.assertIsInstance(store, fms.FileStore)
        self.assertEqual(
            os.path.abspath(store.path),
            os.path.abspath(os.path.join(self.tmp, "docs/forge/constraints.md")),
        )

    def test_constraint_filestore_even_with_no_config_file(self):
        store = fms.select_store(self.tmp, "constraint")
        self.assertIsInstance(store, fms.FileStore)

    def test_absent_config_selects_githubstore(self):
        store = fms.select_store(self.tmp, "deferral")
        self.assertIsInstance(store, fms.GitHubStore)

    def test_explicit_file_config_selects_filestore(self):
        self._write_config({"deferrals": {"store": "file"}})
        store = fms.select_store(self.tmp, "deferral")
        self.assertIsInstance(store, fms.FileStore)
        self.assertEqual(
            os.path.abspath(store.path),
            os.path.abspath(os.path.join(self.tmp, "docs/forge/deferrals.md")),
        )

    def test_explicit_github_config_selects_githubstore(self):
        self._write_config({"deferrals": {"store": "github"}})
        store = fms.select_store(self.tmp, "deferral")
        self.assertIsInstance(store, fms.GitHubStore)

    def test_unknown_store_value_raises_naming_legal_values(self):
        self._write_config({"deferrals": {"store": "sqlite"}})
        with self.assertRaises(fms.ConfigError) as cm:
            fms.select_store(self.tmp, "deferral")
        msg = str(cm.exception)
        self.assertIn("sqlite", msg)
        self.assertIn("github", msg)
        self.assertIn("file", msg)

    def test_malformed_config_raises_naming_file(self):
        path = self._write_config("{not valid json")
        with self.assertRaises(fms.ConfigError) as cm:
            fms.select_store(self.tmp, "deferral")
        self.assertIn(path, str(cm.exception))

    def test_top_level_array_config_raises_naming_file(self):
        path = self._write_config("[1, 2, 3]")
        with self.assertRaises(fms.ConfigError) as cm:
            fms.select_store(self.tmp, "deferral")
        self.assertIn(path, str(cm.exception))

    def test_top_level_string_config_raises_naming_file(self):
        path = self._write_config('"github"')
        with self.assertRaises(fms.ConfigError) as cm:
            fms.select_store(self.tmp, "deferral")
        self.assertIn(path, str(cm.exception))

    def test_top_level_null_config_raises_naming_file(self):
        path = self._write_config("null")
        with self.assertRaises(fms.ConfigError) as cm:
            fms.select_store(self.tmp, "deferral")
        self.assertIn(path, str(cm.exception))

    def test_deferrals_value_not_object_raises_naming_file(self):
        path = self._write_config({"deferrals": "file"})
        with self.assertRaises(fms.ConfigError) as cm:
            fms.select_store(self.tmp, "deferral")
        self.assertIn(path, str(cm.exception))


def _completed(returncode=0, stdout="", stderr=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class GitHubStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-gh-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_gh_absent_raises_storeunavailable_naming_install(self):
        store = fms.GitHubStore(self.tmp)
        with mock.patch.object(fms.shutil, "which", return_value=None):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral())
        msg = str(cm.exception)
        self.assertIn("gh", msg)
        self.assertIn("install", msg.lower())
        self.assertNotIn("file store", msg.lower())
        self.assertNotIn("fall back", msg.lower())

    def test_gh_unauthenticated_raises_naming_auth_login(self):
        store = fms.GitHubStore(self.tmp)
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run") as run:
            run.return_value = _completed(returncode=1, stderr="not logged in")
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral())
        self.assertIn("gh auth login", str(cm.exception))

    def test_issues_disabled_raises_naming_fix(self):
        store = fms.GitHubStore(self.tmp)

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(
                returncode=1,
                stderr="GraphQL: Issues has been disabled in this repository (createIssue)",
            )

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral())
        msg = str(cm.exception).lower()
        self.assertIn("issues", msg)
        self.assertIn("enable", msg)

    def test_create_invokes_gh_issue_create_with_body_and_labels(self):
        store = fms.GitHubStore(self.tmp)
        record = _deferral(title="fix-the-thing", follow_up="revisit-when:q4-audit")
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="https://github.com/o/r/issues/42\n")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            ref = store.create(record)

        self.assertEqual(ref, "42")
        create_call = next(a for a in calls if a[:2] == ["gh", "issue"] and "create" in a)
        self.assertIn(forge_memory.render(record), create_call)
        self.assertIn(fms.GitHubStore.LABEL, create_call)
        # The follow-up label comes from a fixed, closed set. The raw
        # `revisit-when:<condition>` value has unbounded cardinality and can
        # never pre-exist as a label — its full text lives in the rendered
        # body, where the parser reads it back from.
        self.assertIn(fms.GitHubStore.FOLLOW_UP_LABELS["revisit"], create_call)
        self.assertNotIn("revisit-when:q4-audit", create_call)
        self.assertIn("revisit-when:q4-audit", forge_memory.render(record))

    def _fake_gh(self, calls, create_stdout="https://github.com/o/r/issues/42\n",
                 label_returncode=0, label_stderr=""):
        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            if args[:2] == ["gh", "label"]:
                return _completed(returncode=label_returncode, stderr=label_stderr)
            return _completed(returncode=0, stdout=create_stdout)
        return fake_run

    def test_create_ensures_every_label_it_uses_exists(self):
        # Nothing else creates these labels, and `gh issue create` rejects an
        # unknown one — so the default deferral path failed on first use.
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            store.create(_deferral(follow_up="drop"))

        label_calls = [a for a in calls if a[:3] == ["gh", "label", "create"]]
        created = {a[3] for a in label_calls}
        self.assertIn(fms.GitHubStore.LABEL, created)
        self.assertIn(fms.GitHubStore.FOLLOW_UP_LABELS["drop"], created)
        # Idempotent: creating an existing label must not fail the run.
        for call in label_calls:
            self.assertIn("--force", call)
        create_call = next(a for a in calls if a[:2] == ["gh", "issue"])
        for call in label_calls:
            self.assertLess(calls.index(call), calls.index(create_call))

    def test_label_set_is_closed_and_bounded(self):
        self.assertEqual(
            sorted(fms.GitHubStore.FOLLOW_UP_LABELS),
            ["backlog", "drop", "revisit"],
        )
        for value, expected in (
            ("backlog", fms.GitHubStore.FOLLOW_UP_LABELS["backlog"]),
            ("drop", fms.GitHubStore.FOLLOW_UP_LABELS["drop"]),
            ("revisit-when:anything at all", fms.GitHubStore.FOLLOW_UP_LABELS["revisit"]),
        ):
            self.assertEqual(fms._follow_up_label(value), expected)

    def test_label_creation_failure_names_the_fix(self):
        store = fms.GitHubStore(self.tmp)
        calls = []
        fake = self._fake_gh(calls, label_returncode=1,
                             label_stderr="HTTP 403: Resource not accessible")
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral())
        msg = str(cm.exception)
        self.assertIn(fms.GitHubStore.LABEL, msg)
        self.assertIn("gh label create", msg)
        self.assertFalse(
            [a for a in calls if a[:2] == ["gh", "issue"]],
            "must not attempt the issue without its labels",
        )

    def test_create_validates_like_the_file_store(self):
        # FileStore.create validates; GitHubStore.create must too, or a
        # non-CLI caller can push an over-budget record into an issue body
        # that only CI would catch.
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            with self.assertRaises(forge_memory.SchemaError) as cm:
                store.create(_deferral(why="x" * 301))
        msg = str(cm.exception)
        self.assertIn("why", msg)
        self.assertIn("300", msg)
        self.assertFalse(calls, "an invalid record must never reach gh")

    def test_create_rejects_a_record_the_parser_could_not_read_back(self):
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            with self.assertRaises(forge_memory.SchemaError):
                store.create(_deferral(why="one\ntwo"))
        self.assertFalse(calls)

    def test_list_parses_issue_bodies_through_shared_parse(self):
        store = fms.GitHubStore(self.tmp)
        record = _deferral(title="parsed-item")
        body = forge_memory.render(record)
        payload = json.dumps([{"number": 7, "title": "parsed-item", "body": body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            records = store.list("deferral")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["title"], "parsed-item")

    def test_hand_edited_issue_body_out_of_canonical_form_is_reported(self):
        # A hand-edited body that no longer matches the canonical render/parse
        # grammar must be caught the same way fmt_check catches a malformed
        # file: SchemaError propagates rather than being silently ignored.
        store = fms.GitHubStore(self.tmp)
        broken_body = "## parsed-item\nthis is not a valid field line\n"
        payload = json.dumps([{"number": 7, "title": "parsed-item", "body": broken_body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(forge_memory.SchemaError):
                store.list("deferral")

    def test_list_output_validates_like_fmt_check(self):
        # A budget-overrunning field survives parse but is still caught by
        # forge_memory.validate over the parsed record — the same defect
        # fmt_check would report for a file-backed record.
        store = fms.GitHubStore(self.tmp)
        record = _deferral(title="x" * 90)  # exceeds the 80-char title budget
        body = "## {}\n**Why:** {}\n**From:** {}\n**Follow-up:** {}\n".format(
            record.fields["title"], record.fields["why"], record.fields["from"],
            record.fields["follow-up"],
        )
        payload = json.dumps([{"number": 8, "title": "x", "body": body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            records = store.list("deferral")

        defects = forge_memory.validate(records[0])
        self.assertTrue(any("budget" in d for d in defects))

    def test_list_preserves_issue_number_as_ref(self):
        store = fms.GitHubStore(self.tmp)
        body = forge_memory.render(_deferral(title="parsed-item"))
        payload = json.dumps([{"number": 7, "title": "parsed-item", "body": body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            records = store.list("deferral")

        self.assertEqual(records[0].ref, "7")

    def test_list_state_defaults_to_all(self):
        store = fms.GitHubStore(self.tmp)
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="[]")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            store.list("deferral")

        list_call = next(a for a in calls if a[:2] == ["gh", "issue"] and "list" in a)
        self.assertIn("all", list_call)

    def test_list_accepts_a_state_filter(self):
        store = fms.GitHubStore(self.tmp)
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="[]")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            store.list("deferral", state="open")

        list_call = next(a for a in calls if a[:2] == ["gh", "issue"] and "list" in a)
        self.assertIn("open", list_call)
        self.assertNotIn("all", list_call)

    def test_list_default_still_raises_on_first_unparsable_body(self):
        # Unchanged default behavior (errors=None): the same case
        # test_hand_edited_issue_body_out_of_canonical_form_is_reported
        # covers above, re-asserted here to anchor the "collect" mode's
        # contrast below.
        store = fms.GitHubStore(self.tmp)
        broken_body = "## bad\nnot a valid field line\n"
        payload = json.dumps([{"number": 9, "body": broken_body}])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(forge_memory.SchemaError):
                store.list("deferral")

    def test_list_errors_param_collects_all_bad_issues_without_raising(self):
        # Two independently unparsable bodies plus one good one: passing
        # an `errors` list opts into collect-all-defects mode instead of
        # raising on the first, mirroring the "report every defect, never
        # just the first" rule the rest of forge_memory follows. Good
        # records still come back, each still carrying its ref.
        store = fms.GitHubStore(self.tmp)
        good_body = forge_memory.render(_deferral(title="good-one"))
        payload = json.dumps([
            {"number": 1, "body": "## bad-one\nnot a valid field line\n"},
            {"number": 2, "body": good_body},
            {"number": 3, "body": "## bad-two\nalso not valid\n"},
        ])

        def fake_run(args, **kwargs):
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout=payload)

        errors = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            records = store.list("deferral", errors=errors)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["title"], "good-one")
        self.assertEqual(records[0].ref, "2")
        refs_with_errors = {ref for ref, _ in errors}
        self.assertEqual(refs_with_errors, {"1", "3"})

    def test_retire_closes_issue_with_reason_as_comment(self):
        store = fms.GitHubStore(self.tmp)
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            return _completed(returncode=0, stdout="")

        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake_run):
            store.retire("42", reason="superseded by task 9")

        close_call = next(a for a in calls if a[:2] == ["gh", "issue"] and "close" in a)
        self.assertIn("42", close_call)
        self.assertIn("--comment", close_call)
        self.assertIn("superseded by task 9", close_call)


class RenderAllSharedHelperTests(unittest.TestCase):
    """One serializer for whole-file text. It lived verbatim in both modules;
    the store already imports forge_memory, so it uses that one."""

    def test_store_has_no_second_render_all_definition(self):
        source = inspect.getsource(fms)
        self.assertNotIn(
            "def _render_all", source,
            "forge_memory_store must reuse forge_memory._render_all, not "
            "define a second copy",
        )
        self.assertIn("_render_all", inspect.getsource(fms.FileStore._write))
        self.assertIn("_render_all", inspect.getsource(forge_memory.fmt_write))

    def test_file_store_writes_forge_memory_canonical_text(self):
        tmp = tempfile.mkdtemp(prefix="forge-memory-render-all-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "deferrals.md")
        store = fms.FileStore(path)
        first, second = _deferral(title="one"), _deferral(title="two")
        store.create(first)
        store.create(second)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), forge_memory._render_all([first, second]))


if __name__ == "__main__":
    unittest.main()
