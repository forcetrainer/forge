#!/usr/bin/env python3
"""forge_memory_store — where records created by forge_memory.py live.

Two implementations of one interface (``create``/``list``/``retire``):

- ``FileStore`` — canonical markdown, machine-written only. Used for
  ``constraint`` unconditionally, and for ``deferral`` only when the repo has
  explicitly opted out of GitHub via ``docs/forge/config.json``.
- ``GitHubStore`` — the default for ``deferral``. ``gh issue create/list/close
  --json`` under the hood; issue bodies are produced by ``forge_memory.render``
  — the *same* renderer ``FileStore`` uses — so ``forge_memory.fmt_check``
  validates open ``forge:deferral`` issues the same way it validates files,
  and ``GitHubStore.list`` reads them back through ``forge_memory.parse``.

The one rule that matters more than any other here: every store failure is
loud and names its fix. A missing/unauthenticated ``gh``, or a repo with
Issues disabled, never silently slides to the file store — the file store is
reachable only through committed, explicit config (see ``select_store``).
"""
import json
import os
import shutil
import subprocess

import forge_memory


class StoreUnavailable(Exception):
    """The selected store cannot be used right now. Message names the fix —
    never suggests an automatic fallback to another store."""


class ConfigError(Exception):
    """``docs/forge/config.json`` is malformed or names an unsupported store.
    Message names the file and, for an unsupported value, the legal ones."""


class Store:
    def create(self, record):
        raise NotImplementedError

    def scan(self, type, **filters):
        """(records, errors): every record this store holds that could be
        parsed, plus an (ref, message) entry for every one that could not.
        Never raises on a bad record — the collect-every-defect
        counterpart to ``list``, and the one method a caller needing that
        (fmt --check over open issues, list-deferrals) can call without
        knowing which store it holds."""
        raise NotImplementedError

    def list(self, type, **filters):
        """Every record this store holds. A thin wrapper over ``scan``:
        raises the moment ``scan`` reports even one error, rather than
        returning a partial result silently."""
        raise NotImplementedError

    def retire(self, ref, reason=None):
        raise NotImplementedError

    def describe_ref(self, ref):
        """How this store names one ``ref`` from a ``scan`` error or a
        ``Record.ref``, for a message a human reads. A ref is
        store-specific — an issue number here, a file line number there —
        so the store that produced it is the only thing that can label it
        accurately; a caller printing a bare "#{ref}" rendered a file's
        line 1 as issue 1."""
        raise NotImplementedError


def _matches(record, filters):
    return all(record.fields.get(k) == v for k, v in filters.items())


class FileStore(Store):
    """Canonical markdown at ``path``. The record type is fixed by the file
    itself (``constraints.md`` holds ``constraint`` records, ``deferrals.md``
    holds ``deferral`` records) via ``forge_memory``'s existing filename
    convention, reused rather than re-derived."""

    def __init__(self, path):
        self.path = path
        self.type = forge_memory._infer_type(path)

    def _read(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return []
        return forge_memory.parse(text, self.type)

    def _write(self, records):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            # ``forge_memory``'s serializer, not a second copy of it: a file
            # this store writes and a file ``fmt --write`` rewrites must be
            # byte-identical, and one definition is the only way to keep
            # them so.
            f.write(forge_memory._render_all(records))

    def _heading_field(self):
        return forge_memory.SCHEMA[self.type][0].name

    def create(self, record):
        if record.type != self.type:
            raise ValueError(
                "FileStore at {!r} holds {!r} records, got {!r}".format(
                    self.path, self.type, record.type,
                )
            )
        defects = forge_memory.validate(record)
        if defects:
            raise forge_memory.SchemaError(
                "refusing to store an invalid record: {}".format("; ".join(defects))
            )
        heading = self._heading_field()
        ref = record.fields.get(heading)
        records = self._read()
        if any(r.fields.get(heading) == ref for r in records):
            raise forge_memory.SchemaError(
                "{}: a record with {} {!r} already exists".format(
                    self.path, heading, ref,
                )
            )
        records.append(record)
        self._write(records)
        return ref

    def scan(self, type, **filters):
        """(records, errors), same shape as ``GitHubStore.scan``, but
        all-or-nothing rather than per-record: ``forge_memory.parse``
        reads the whole file as one atomic pass over shared state (a
        heading opens a record, a duplicate id or bad line anywhere aborts
        the whole parse), so there is no independently-parsed good record
        to salvage the way there is for each of GitHubStore's separately
        parsed issue bodies — a parse failure here always yields zero
        records alongside the one error."""
        if type != self.type:
            raise ValueError(
                "FileStore at {!r} holds {!r} records, got {!r}".format(
                    self.path, self.type, type,
                )
            )
        try:
            records = self._read()
        except forge_memory.SchemaError as e:
            ref = str(e.line) if e.line is not None else None
            return [], [(ref, str(e))]
        return [r for r in records if _matches(r, filters)], []

    def list(self, type, **filters):
        records, errors = self.scan(type, **filters)
        if errors:
            _, message = errors[0]
            raise forge_memory.SchemaError(message)
        return records

    def describe_ref(self, ref):
        """The file. ``FileStore``'s refs are line numbers, and every
        message they accompany comes from ``forge_memory.parse``, which
        already names the line — what the reader is missing is which file
        it is in."""
        return self.path

    def retire(self, ref, reason=None):
        heading = self._heading_field()
        records = self._read()
        remaining = [r for r in records if r.fields.get(heading) != ref]
        if len(remaining) == len(records):
            raise forge_memory.SchemaError(
                "{}: no record with {} {!r} to retire".format(self.path, heading, ref)
            )
        self._write(remaining)


def _gh_ready(cwd):
    """Preflight: ``gh`` on PATH and authenticated. Raises ``StoreUnavailable``
    naming the fix — install ``gh``, or ``gh auth login`` — never a silent
    slide to the file store."""
    if shutil.which("gh") is None:
        raise StoreUnavailable(
            "gh CLI not found on PATH. Install it from https://cli.github.com/ "
            "and re-run."
        )
    proc = subprocess.run(
        ["gh", "auth", "status"], cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise StoreUnavailable(
            "gh CLI is not authenticated. Run `gh auth login` and re-run."
        )


def _issues_disabled(proc):
    stderr = (proc.stderr or "").lower()
    return "issue" in stderr and "disabled" in stderr


def _raise_for_gh_failure(proc):
    stderr = (proc.stderr or "").strip()
    if _issues_disabled(proc):
        raise StoreUnavailable(
            "Issues are disabled for this repository. Enable Issues in the "
            "repo settings (Settings > General > Features) and re-run. "
            "(gh said: {})".format(stderr)
        )
    raise StoreUnavailable("gh command failed: {}".format(stderr or proc.returncode))


def _follow_up_label(value):
    """The ``follow-up`` field value -> its label in the closed set above.
    ``revisit-when:<condition>`` has unbounded cardinality, so the condition
    itself is never a label — it lives in the rendered body, which is where
    ``list`` reads it back from. Returns ``None`` for a value outside the
    schema's enum, which ``validate`` has already rejected by the time
    ``create`` gets here."""
    if value in ("backlog", "drop"):
        return GitHubStore.FOLLOW_UP_LABELS[value]
    if value.startswith("revisit-when:"):
        return GitHubStore.FOLLOW_UP_LABELS["revisit"]
    return None


class GitHubStore(Store):
    """``gh issue create/list/close --json`` backend for ``deferral``
    records. Issue bodies are ``forge_memory.render`` output — the same
    renderer ``FileStore`` uses — and are read back through
    ``forge_memory.parse``."""

    LABEL = "forge:deferral"

    # A fixed, closed label set. `gh issue create` rejects a label the repo
    # does not have, and nothing but this store ever creates one — so the
    # labels it uses must be few enough to create up front, which rules out
    # labelling with the raw follow-up value (`revisit-when:<condition>` is
    # unbounded and could never pre-exist).
    FOLLOW_UP_LABELS = {
        "backlog": "forge:backlog",
        "drop": "forge:drop",
        "revisit": "forge:revisit",
    }

    _LABEL_DESCRIPTIONS = {
        LABEL: "Work deferred through forge_memory.py defer",
        FOLLOW_UP_LABELS["backlog"]: "Deferred: stays open in the backlog",
        FOLLOW_UP_LABELS["drop"]: "Deferred: dropped unless it comes back",
        FOLLOW_UP_LABELS["revisit"]:
            "Deferred: revisit when the condition in the body is met",
    }

    def __init__(self, repo_root):
        self.repo_root = repo_root

    def _run(self, args):
        return subprocess.run(
            ["gh"] + args, cwd=self.repo_root, capture_output=True, text=True,
        )

    def _ensure_labels(self, labels):
        """Create each label if missing, before the issue that needs it.
        ``--force`` makes this idempotent (it updates an existing label
        instead of erroring), so a repo that already has them is a no-op.
        A failure here is loud and names the one-time manual fix — never a
        silent slide to an unlabelled issue or to the file store."""
        for name in labels:
            proc = self._run([
                "label", "create", name,
                "--description", self._LABEL_DESCRIPTIONS[name],
                "--force",
            ])
            if proc.returncode != 0:
                # A repo with Issues disabled fails here first, on the
                # label rather than the issue; it is still that failure and
                # still names that fix, not a misleading label message.
                if _issues_disabled(proc):
                    _raise_for_gh_failure(proc)
                stderr = (proc.stderr or "").strip()
                raise StoreUnavailable(
                    "could not ensure the label {0!r} exists, which "
                    "`gh issue create` requires (gh said: {1}). Create it "
                    "once with `gh label create {0}` — or have someone with "
                    "write access to this repo do it — then re-run.".format(
                        name, stderr or proc.returncode,
                    )
                )

    def create(self, record):
        # Validated here, not only in the CLI: FileStore.create validates,
        # and the file store is "a second drawer, not a degraded path" —
        # the symmetry has to hold in both directions, or a non-CLI caller
        # pushes an over-budget body that only CI would catch.
        defects = forge_memory.validate(record)
        if defects:
            raise forge_memory.SchemaError(
                "refusing to store an invalid record: {}".format("; ".join(defects))
            )
        _gh_ready(self.repo_root)
        body = forge_memory.render(record)
        title = record.fields.get("title", "")
        labels = [self.LABEL]
        follow_up_label = _follow_up_label(record.fields.get("follow-up", ""))
        if follow_up_label:
            labels.append(follow_up_label)
        self._ensure_labels(labels)
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            args += ["--label", label]
        proc = self._run(args)
        if proc.returncode != 0:
            _raise_for_gh_failure(proc)
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        number = url.rstrip("/").rsplit("/", 1)[-1]
        return number

    def describe_ref(self, ref):
        return "issue #{}".format(ref)

    def scan(self, type, state="all", **filters):
        """Returns ``(records, errors)`` — the same shape ``FileStore.scan``
        returns. Every issue's body is parsed back through
        ``forge_memory.parse``, each returned ``Record`` carrying its issue
        number as ``.ref`` (the public way a caller — ``fmt``'s open-issue
        check included — recovers which issue a record came from; this is
        the only ``gh issue list`` call site in the codebase).

        An unparsable body never aborts the call: it appends an
        ``(issue_number, message)`` pair to the returned ``errors`` instead,
        so one bad issue never hides another. The records that DO parse are
        still returned (and still subject to ``forge_memory.validate`` by
        the caller, the same as a good record from a bad file would be)."""
        _gh_ready(self.repo_root)
        proc = self._run([
            "issue", "list", "--label", self.LABEL, "--state", state,
            "--json", "number,title,body",
        ])
        if proc.returncode != 0:
            _raise_for_gh_failure(proc)
        data = json.loads(proc.stdout or "[]")
        records = []
        errors = []
        for item in data:
            ref = str(item.get("number"))
            try:
                parsed = forge_memory.parse(item.get("body", ""), type)
            except forge_memory.SchemaError as e:
                errors.append((ref, str(e)))
                continue
            for r in parsed:
                r.ref = ref
                records.append(r)
        return [r for r in records if _matches(r, filters)], errors

    def list(self, type, state="all", **filters):
        """Thin wrapper over ``scan``: raises the moment it reports any
        error — one parse path, not two — exactly ``FileStore.list``'s
        contract, so a caller holding either store gets the same behavior
        from ``list``."""
        records, errors = self.scan(type, state=state, **filters)
        if errors:
            ref, message = errors[0]
            raise forge_memory.SchemaError(
                "issue #{}: {}".format(ref, message)
            )
        return records

    def retire(self, ref, reason=None):
        _gh_ready(self.repo_root)
        args = ["issue", "close", str(ref)]
        if reason:
            args += ["--comment", reason]
        proc = self._run(args)
        if proc.returncode != 0:
            _raise_for_gh_failure(proc)


_LEGAL_STORE_VALUES = ("github", "file")


def select_store(repo_root, type):
    """``constraint`` is always ``FileStore(docs/forge/constraints.md)``,
    ignoring config entirely. ``deferral`` reads ``docs/forge/config.json``:
    absent => GitHub; ``{"deferrals":{"store":"file"}}`` => FileStore;
    anything else (unknown value, or malformed JSON) is a loud ``ConfigError``
    naming the file and the legal values — never a silent fallback. No other
    config keys are read."""
    if type == "constraint":
        return FileStore(os.path.join(repo_root, "docs/forge/constraints.md"))

    if type != "deferral":
        raise ValueError("select_store: unknown record type {!r}".format(type))

    config_path = os.path.join(repo_root, "docs/forge/config.json")
    if not os.path.exists(config_path):
        return GitHubStore(repo_root)

    with open(config_path, encoding="utf-8") as f:
        text = f.read()
    try:
        config = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(
            "{}: malformed JSON ({}). Expected "
            '{{"deferrals": {{"store": "github"|"file"}}}}.'.format(config_path, e)
        )

    if not isinstance(config, dict):
        raise ConfigError(
            "{}: expected a top-level JSON object "
            '({{"deferrals": {{"store": "github"|"file"}}}}), got {}'.format(
                config_path, config.__class__.__name__,
            )
        )

    store_value = "github"
    deferrals = config.get("deferrals")
    if deferrals is not None:
        if not isinstance(deferrals, dict):
            raise ConfigError(
                '{}: "deferrals" must be a JSON object '
                '({{"store": "github"|"file"}}), got {}'.format(
                    config_path, deferrals.__class__.__name__,
                )
            )
        if "store" in deferrals:
            store_value = deferrals["store"]

    if store_value not in _LEGAL_STORE_VALUES:
        raise ConfigError(
            "{}: deferrals.store {!r} is not a legal value — expected one of "
            "{}".format(config_path, store_value, list(_LEGAL_STORE_VALUES))
        )

    if store_value == "file":
        return FileStore(os.path.join(repo_root, "docs/forge/deferrals.md"))
    return GitHubStore(repo_root)


def managed_paths(repo_root):
    """The managed local project-memory files under ``repo_root``:
    ``docs/forge/constraints.md`` when it exists, plus ``docs/forge/
    deferrals.md`` only when config selects the file store for deferrals.
    A managed file that does not exist is simply absent from the list —
    most repos will never have these files, and lacking them is not a
    defect.

    The single definition of "what are the managed paths", called by both
    ``forge_memory``'s ``fmt`` and ``forge_lint.check_memory_files``; a
    third managed file is added here once and both callers pick it up. It
    lives in this module because the answer is store selection, which is
    this module's job — and because both callers already import it, so
    sharing costs no new import edge.

    Never calls ``gh``: ``select_store`` only *constructs* a
    ``GitHubStore``, it never invokes one, so this is safe on the offline
    path (a pre-commit hook) even when the GitHub store is selected.
    Raises ``ConfigError`` on malformed config, same as ``select_store``.
    """
    paths = []

    constraints_path = os.path.join(repo_root, "docs/forge/constraints.md")
    if os.path.exists(constraints_path):
        paths.append(constraints_path)

    deferral_store = select_store(repo_root, "deferral")
    if isinstance(deferral_store, FileStore) and os.path.exists(deferral_store.path):
        paths.append(deferral_store.path)

    return paths
