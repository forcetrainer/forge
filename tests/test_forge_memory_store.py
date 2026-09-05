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


def _deferral(title="fix-the-thing", why="Needed later.", frm="user"):
    return forge_memory.Record(
        type="deferral",
        fields={
            "title": title,
            "why": why,
            "from": frm,
        },
    )


def _constraint(id_="no-network-calls", rule="Never call the network.",
                scope="repo", because="Determinism.", source="user"):
    return forge_memory.Record(
        type="constraint",
        fields={
            "id": id_,
            "rule": rule,
            "scope": scope,
            "because": because,
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

    def test_scan_returns_records_and_empty_errors_when_file_parses(self):
        store = fms.FileStore(self.path)
        store.create(_deferral(title="first-item"))
        records, errors = store.scan("deferral")
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].fields["title"], "first-item")

    def test_scan_reports_an_unparsable_file_as_an_error_naming_its_line(self):
        # scan never raises: an unparsable file yields no records and one
        # (ref, message) error entry, its ref the line number named in the
        # parse failure — the file equivalent of a GitHub issue number.
        _write = open(self.path, "w", encoding="utf-8")
        _write.write("## alpha\nthis is not a valid field line\n")
        _write.close()
        store = fms.FileStore(self.path)
        records, errors = store.scan("deferral")
        self.assertEqual(records, [])
        self.assertEqual(len(errors), 1)
        ref, msg = errors[0]
        self.assertEqual(ref, "2")
        self.assertIn("line 2", msg)

    def test_scan_ref_comes_from_the_structured_line_attribute_not_the_message(self):
        # Proof this isn't scraped back out of the message text: reword the
        # message forge_memory.parse would raise (no "line N:" prefix at
        # all) while still setting the structured `.line` attribute, and
        # confirm FileStore.scan's ref still comes through correctly. A
        # regex over the message text would yield None here.
        store = fms.FileStore(self.path)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("anything, since parse is stubbed below\n")

        def fake_parse(text, record_type):
            raise forge_memory.SchemaError(
                "totally reworded message with no digits nearby", line=5,
            )

        with mock.patch.object(forge_memory, "parse", side_effect=fake_parse):
            records, errors = store.scan("deferral")

        self.assertEqual(records, [])
        ref, msg = errors[0]
        self.assertEqual(ref, "5")
        self.assertEqual(msg, "totally reworded message with no digits nearby")

    def test_list_raises_on_what_scan_reports_as_an_error(self):
        _write = open(self.path, "w", encoding="utf-8")
        _write.write("## alpha\nthis is not a valid field line\n")
        _write.close()
        store = fms.FileStore(self.path)
        with self.assertRaises(forge_memory.SchemaError):
            store.list("deferral")


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
                store.create(_deferral(), by="agent")
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
                store.create(_deferral(), by="agent")
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
                store.create(_deferral(), by="agent")
        msg = str(cm.exception).lower()
        self.assertIn("issues", msg)
        self.assertIn("enable", msg)

    def _fake_gh(self, calls, create_stdout="https://github.com/o/r/issues/42\n",
                 existing_labels=(), label_create_returncode=0,
                 label_create_stderr="", label_list_returncode=0,
                 label_list_stdout=None, label_list_stderr=""):
        """A stubbed gh. ``existing_labels`` is what `gh label list --json
        name` reports — the structured answer the store asks for, never an
        error string it has to read."""
        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["gh", "auth"]:
                return _completed(returncode=0, stdout="Logged in")
            if args[:3] == ["gh", "label", "list"]:
                stdout = label_list_stdout
                if stdout is None:
                    stdout = json.dumps([{"name": n} for n in existing_labels])
                return _completed(
                    returncode=label_list_returncode, stdout=stdout,
                    stderr=label_list_stderr,
                )
            if args[:3] == ["gh", "label", "create"]:
                return _completed(
                    returncode=label_create_returncode,
                    stderr=label_create_stderr,
                )
            return _completed(returncode=0, stdout=create_stdout)
        return fake_run

    def test_create_applies_exactly_one_origin_label_and_no_forge_label(self):
        # The label model is six human-meaningful labels: four kinds
        # (feature/defect/debt/risk) and two origins (by:human/by:agent).
        # The engine applies the origin and nothing else — kind is a human
        # judgment the runner cannot make, and `forge:deferral` existed only
        # as a retrieval marker for read-back features that are now gone.
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            ref = store.create(_deferral(), by="agent")

        self.assertEqual(ref, "42")
        create = next(a for a in calls if a[:2] == ["gh", "issue"] and "create" in a)
        labels = [create[i + 1] for i, v in enumerate(create) if v == "--label"]
        self.assertEqual(labels, ["by:agent"])
        self.assertFalse([a for a in create if a.startswith("forge:")], create)
        self.assertIn(forge_memory.render(_deferral()), create)

    def test_create_applies_by_human_when_the_user_authored_it(self):
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            store.create(_deferral(), by="human")
        create = next(a for a in calls if a[:2] == ["gh", "issue"] and "create" in a)
        labels = [create[i + 1] for i, v in enumerate(create) if v == "--label"]
        self.assertEqual(labels, ["by:human"])

    def test_create_without_an_origin_is_refused_before_gh(self):
        # Origin is required and unguessable. Defaulting it would put a
        # wrong `by:` label on a real issue, which is worse than refusing.
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            for bad in (None, "robot", ""):
                with self.subTest(by=bad):
                    with self.assertRaises(forge_memory.SchemaError) as cm:
                        store.create(_deferral(), by=bad)
                    self.assertIn("human", str(cm.exception))
                    self.assertIn("agent", str(cm.exception))
        self.assertFalse(calls, "an unlabellable record must never reach gh")

    def test_origin_label_set_is_exactly_two(self):
        self.assertEqual(
            fms.GitHubStore.ORIGIN_LABELS,
            {"human": "by:human", "agent": "by:agent"},
        )

    def test_create_creates_a_missing_origin_label_before_the_issue(self):
        # Nothing else creates these labels and `gh issue create` rejects an
        # unknown one, so the first filing in a fresh repo would fail on a
        # label the user never heard of — at the close-out gate, after every
        # task has passed. Create the missing one, before the issue.
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(
                                   calls, existing_labels=["bug", "by:human"])):
            store.create(_deferral(), by="agent")

        label_calls = [a for a in calls if a[:3] == ["gh", "label", "create"]]
        self.assertEqual([a[3] for a in label_calls], ["by:agent"])
        issue_call = next(a for a in calls if a[:2] == ["gh", "issue"])
        self.assertLess(calls.index(label_calls[0]), calls.index(issue_call))
        self.assertNotIn("--force", label_calls[0])

    def test_an_existing_label_is_detected_by_query_not_by_error_text(self):
        # Existence is answered by `gh label list --json name` — structured
        # output — so nothing depends on the wording of a gh error message.
        # The stub below fails any `label create` with a message that says
        # nothing about existing; the store must never reach it.
        store = fms.GitHubStore(self.tmp)
        calls = []
        fake = self._fake_gh(
            calls, existing_labels=["by:agent", "debt"],
            label_create_returncode=1,
            label_create_stderr="ceci n'est pas un message en anglais",
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake):
            ref = store.create(_deferral(), by="agent")

        self.assertEqual(ref, "42")
        self.assertFalse(
            [a for a in calls if a[:3] == ["gh", "label", "create"]],
            "an existing label must not be re-created or updated",
        )
        list_call = next(a for a in calls if a[:3] == ["gh", "label", "list"])
        self.assertIn("--json", list_call)
        self.assertIn("name", list_call)
        self.assertTrue([a for a in calls if a[:2] == ["gh", "issue"]])

    def test_existence_is_never_decided_by_matching_gh_prose(self):
        # Scoped to GitHubStore: FileStore's duplicate-id message says
        # "already exists" legitimately, about its own file.
        source = inspect.getsource(fms.GitHubStore)
        self.assertNotIn("already exists", source)

    def test_label_creation_failure_says_so_and_names_the_label(self):
        # F6: create-if-missing makes label-write permission a precondition
        # for filing when the label is absent. Someone with issue-write but
        # not label-write must be told exactly that, and what to ask for.
        store = fms.GitHubStore(self.tmp)
        calls = []
        fake = self._fake_gh(calls, label_create_returncode=1,
                             label_create_stderr="HTTP 403: Resource not accessible")
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral(), by="agent")
        msg = str(cm.exception)
        self.assertIn("could not create the label", msg)
        self.assertIn("by:agent", msg)
        self.assertIn("gh label create by:agent", msg)
        self.assertIn("permission", msg.lower())
        self.assertFalse(
            [a for a in calls if a[:2] == ["gh", "issue"]],
            "must not attempt the issue without its label",
        )

    def test_label_query_failure_is_loud_and_names_the_label(self):
        store = fms.GitHubStore(self.tmp)
        calls = []
        fake = self._fake_gh(calls, label_list_returncode=1,
                             label_list_stderr="HTTP 500")
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral(), by="agent")
        msg = str(cm.exception)
        self.assertIn("by:agent", msg)
        self.assertFalse([a for a in calls if a[:2] == ["gh", "issue"]])
        self.assertFalse([a for a in calls if a[:3] == ["gh", "label", "create"]])

    def test_unreadable_label_query_output_is_loud_not_assumed_absent(self):
        # Unparsable JSON must not be read as "no labels" — that would
        # attempt a create on every filing.
        store = fms.GitHubStore(self.tmp)
        calls = []
        fake = self._fake_gh(calls, label_list_stdout="not json at all")
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral(), by="agent")
        self.assertIn("by:agent", str(cm.exception))
        self.assertFalse([a for a in calls if a[:2] == ["gh", "issue"]])

    def test_issues_disabled_during_the_label_step_names_that_fix(self):
        store = fms.GitHubStore(self.tmp)
        calls = []
        fake = self._fake_gh(
            calls, label_list_returncode=1,
            label_list_stderr="GraphQL: Issues has been disabled in this repository",
        )
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run", side_effect=fake):
            with self.assertRaises(fms.StoreUnavailable) as cm:
                store.create(_deferral(), by="agent")
        msg = str(cm.exception).lower()
        self.assertIn("issues", msg)
        self.assertIn("enable", msg)

    def test_create_validates_like_the_file_store(self):
        # FileStore.create validates; GitHubStore.create must too, or a
        # non-CLI caller can push an over-budget record into an issue body.
        # This is the half of validation that survives the read-back drop:
        # it stops a malformed record reaching the network.
        store = fms.GitHubStore(self.tmp)
        calls = []
        with mock.patch.object(fms.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(fms.subprocess, "run",
                               side_effect=self._fake_gh(calls)):
            with self.assertRaises(forge_memory.SchemaError) as cm:
                store.create(_deferral(why="x" * 301), by="agent")
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
                store.create(_deferral(why="one\ntwo"), by="agent")
        self.assertFalse(calls)

    def test_github_store_has_no_read_back_path(self):
        # Read-back retrieval is dropped: both features it served
        # (`list-deferrals` and fmt's open-issue check) only re-found the
        # engine's own records and duplicated the GitHub UI.
        for name in ("scan", "list", "describe_ref"):
            with self.subTest(method=name):
                self.assertFalse(
                    name in fms.GitHubStore.__dict__,
                    "GitHubStore.{} must not exist".format(name),
                )

    def test_no_gh_issue_list_call_site_remains(self):
        for module in (fms, forge_memory):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertNotIn('"issue", "list"', source)
                self.assertNotIn("gh issue list", source)

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


class StoreInterfaceUniformityTests(unittest.TestCase):
    """``Store`` declares the operations that are genuinely polymorphic —
    ``create`` and ``retire``, the two a caller makes without knowing which
    store ``select_store`` handed it. Reading records back is not one of
    them any more: ``GitHubStore`` has no read path, and the only remaining
    read caller (``list-constraints``) is hard-wired to ``FileStore`` by
    ``select_store``. A base method exactly one subclass implements
    abstracts nothing and promises a caller something the other subclass
    cannot keep, so ``scan``/``list`` live on ``FileStore``."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-memory-uniform-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_file_store_scan_and_list_shapes_are_unchanged(self):
        file_store = fms.FileStore(os.path.join(self.tmp, "deferrals.md"))
        file_store.create(_deferral(title="a-deferral"))

        records, errors = file_store.scan("deferral")
        self.assertIsInstance(records, list)
        self.assertIsInstance(errors, list)

        listed = file_store.list("deferral")
        self.assertIsInstance(listed, list)
        self.assertEqual(listed[0].fields["title"], "a-deferral")

    def test_base_interface_is_create_and_retire_only(self):
        for name in ("create", "retire"):
            with self.subTest(method=name):
                self.assertIn(name, fms.Store.__dict__)
        for name in ("scan", "list"):
            with self.subTest(method=name):
                self.assertNotIn(
                    name, fms.Store.__dict__,
                    "Store must not declare {} — only FileStore implements "
                    "it, so the base promised what GitHubStore cannot "
                    "keep".format(name),
                )
                self.assertIn(name, fms.FileStore.__dict__)

    def test_module_header_describes_the_interface_it_has(self):
        header = fms.__doc__
        self.assertIn("create", header)
        self.assertIn("retire", header)
        self.assertNotIn("``create``/``list``/``retire``", header)

    def test_scan_docstring_names_a_real_caller(self):
        # It used to name `list-constraints`, which calls `list`, and which
        # select_store answers with FileStore unconditionally — so the
        # claim was wrong in both halves.
        doc = fms.FileStore.scan.__doc__ or ""
        self.assertNotIn("list-constraints", doc)

    def test_no_call_site_branches_on_githubstore(self):
        # cmd_fmt's isinstance check existed to decide whether to read open
        # issues. With that branch gone, forge_memory has no reason left to
        # know which store it holds.
        forge_memory_path = os.path.join(SCRIPTS_DIR, "forge_memory.py")
        with open(forge_memory_path, encoding="utf-8") as f:
            lines = f.readlines()
        branch_lines = [
            lineno for lineno, line in enumerate(lines, start=1)
            if "isinstance(" in line and "GitHubStore" in line
        ]
        self.assertEqual(
            len(branch_lines), 0,
            "no isinstance(..., GitHubStore) check should remain, found at "
            "lines {}".format(branch_lines),
        )


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
