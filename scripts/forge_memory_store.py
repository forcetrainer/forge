#!/usr/bin/env python3
"""forge_memory_store — where records created by forge_memory.py live.

Two implementations of one interface (``create``/``retire``) — the two
operations a caller makes without knowing which store ``select_store``
handed it. Reading records back is NOT part of that interface: only
``FileStore`` can do it, and its one caller (``list-constraints``) is
hard-wired to ``FileStore`` by ``select_store``, so ``scan``/``list``
live on ``FileStore`` rather than being promised by a base class that
``GitHubStore`` could not honour.

- ``FileStore`` — canonical markdown, machine-written only. Used for
  ``constraint`` unconditionally, and for ``deferral`` only when the repo has
  explicitly opted out of GitHub via ``docs/forge/config.json``.
- ``GitHubStore`` — the default for ``deferral``. ``gh issue create/close``
  under the hood; issue bodies are produced by ``forge_memory.render``, the
  *same* renderer ``FileStore`` uses. Write-only: it files and closes, and
  never reads issues back. Re-finding forge's own records duplicated the
  GitHub UI, and validating a body already on GitHub guards a threat that
  does not propagate — the next record is composed from CLI arguments, not
  read from the last one. ``create`` validates BEFORE ``_gh_ready``, which
  is the half that matters: a malformed or over-budget record never
  reaches the network.

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
    def create(self, record, by=None):
        """Store ``record``. ``by`` is the record's ORIGIN (``"human"`` or
        ``"agent"``) — who noticed it. It is not a schema field: on GitHub
        it becomes the issue's ``by:`` label, and a backend with no label
        facility ignores it rather than inventing a field to mirror one."""
        raise NotImplementedError

    def retire(self, ref, reason=None):
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

    def create(self, record, by=None):
        # ``by`` is accepted and dropped: the file backend has no labels,
        # and the record schema has no origin field to put it in. Choosing
        # this backend is an explicit opt-out from issues (see
        # ``select_store``), so it is an opt-out from what issues carry.
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
        """(records, errors): every record in the file that parsed, plus an
        ``(ref, message)`` entry for what did not, ``ref`` being the line
        number. Never raises on a bad file — the collect-every-defect
        counterpart to ``list``, which is its only caller (``FileStore.list``
        below; ``GitHubStore`` has no read path at all, which is why neither
        method is promised by ``Store``).

        All-or-nothing rather than per-record: ``forge_memory.parse`` reads
        the whole file as one atomic pass over shared state (a heading opens
        a record, a duplicate id or bad line anywhere aborts the whole
        parse), so there is no independently-parsed good record to salvage —
        a parse failure here always yields zero records alongside the one
        error."""
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


class GitHubStore(Store):
    """``gh issue create/close`` backend for ``deferral`` records. Issue
    bodies are ``forge_memory.render`` output — the same renderer
    ``FileStore`` uses. Write-only: nothing here reads an issue back.

    Labels: exactly one origin label per filed issue. The KIND labels
    (``feature``/``defect``/``debt``/``risk``) are never applied here —
    whether something is a defect or debt is a human call this engine
    cannot make, and a guessed kind label is worse than an absent one
    because it reads as a judgment somebody made."""

    ORIGIN_LABELS = {
        "human": "by:human",
        "agent": "by:agent",
    }

    _ORIGIN_DESCRIPTIONS = {
        "by:human": "Noticed by a person",
        "by:agent": "Noticed by an agent",
    }

    def __init__(self, repo_root):
        self.repo_root = repo_root

    def _run(self, args):
        return subprocess.run(
            ["gh"] + args, cwd=self.repo_root, capture_output=True, text=True,
        )

    # How many labels to read when checking whether an origin label
    # exists. `gh label list` defaults to 30, which a repo can exceed. A
    # repo with more labels than this is not a silent wrong answer: the
    # label reads as absent, the create is attempted, and gh refuses it —
    # loudly, through the named message below.
    _LABEL_LIST_LIMIT = 1000

    def _label_exists(self, name):
        """Whether the repo already has the label ``name``, answered from
        ``gh label list --json name`` — STRUCTURED output.

        Never from the text of a gh error. Deciding "this label already
        exists" by matching English prose in stderr would turn a reworded
        or localised gh message into a filing that fails on a label the
        repo already has, and that failure would look exactly like a
        permissions problem. The question here has an exact answer
        available, so it is asked exactly.

        Costs one extra `gh` call per filing. Filing happens at a close-out
        gate, a handful of times per run at most, so the call is cheap
        where it lands — and it buys the alternative's avoidance: `gh label
        create --force` needs no query but rewrites an existing label's
        colour and description on every single filing, overwriting whatever
        a human chose (and, with no `--color` passed, re-randomising it).

        Any failure to ASK the question is loud: an unreadable answer is
        never read as "absent", which would attempt a create every time."""
        proc = self._run([
            "label", "list", "--json", "name",
            "--limit", str(self._LABEL_LIST_LIMIT),
        ])
        if proc.returncode != 0:
            # A repo with Issues disabled fails here first, on the label
            # query rather than the issue; it is still that failure and
            # still names that fix, not a misleading label message.
            if _issues_disabled(proc):
                _raise_for_gh_failure(proc)
            raise StoreUnavailable(
                "could not check whether the label {0!r} exists, which "
                "every filed deferral must carry (gh said: {1}). Re-run "
                "once `gh label list` works in this repo.".format(
                    name, (proc.stderr or "").strip() or proc.returncode,
                )
            )
        try:
            data = json.loads(proc.stdout or "[]")
            names = {item["name"] for item in data}
        except (ValueError, TypeError, KeyError) as e:
            raise StoreUnavailable(
                "could not read the label list while checking for {0!r}, "
                "which every filed deferral must carry ({1}). Refusing to "
                "assume the label is absent — that would try to create it "
                "on every filing.".format(name, e)
            )
        return name in names

    def _ensure_label(self, name):
        """Create ``name`` if the repo does not already have it, before the
        issue that needs it.

        Create-if-missing rather than fail-loud, because of the fresh-repo
        case: ``gh issue create`` rejects a label the repo does not have,
        nothing but this store ever creates these two, and the first
        ``defer`` in a repo runs at a close-out gate — after every task and
        the final review have passed. Failing there, on a label the user
        has never heard of, would strand a reviewed deferral behind a
        manual `gh label create` for a name only this code knows. The set
        is closed and tiny (two values), so creating on demand cannot grow
        unbounded the way labelling with a free value would.

        The accepted consequence: when the label is ABSENT, filing needs
        permission to create a repository label, not just to open an issue.
        Someone with issue-write but not label-write cannot file until the
        label exists, so the failure below says exactly that — it names the
        label, says plainly that it could not be created, and gives the
        one-time command someone with write access runs. It is never a
        silent slide to an unlabelled issue or to the file store."""
        if self._label_exists(name):
            return
        proc = self._run([
            "label", "create", name,
            "--description", self._ORIGIN_DESCRIPTIONS[name],
        ])
        if proc.returncode == 0:
            return
        if _issues_disabled(proc):
            _raise_for_gh_failure(proc)
        raise StoreUnavailable(
            "could not create the label {0!r}, which every filed deferral "
            "must carry and which this repository does not have yet (gh "
            "said: {1}). Filing a deferral needs permission to create a "
            "repository label the first time an origin is used — issue "
            "write access alone is not enough. Run `gh label create {0}` "
            "once, or ask someone with write access to this repo to, then "
            "re-run.".format(name, (proc.stderr or "").strip() or proc.returncode)
        )

    def create(self, record, by=None):
        # Validated here, not only in the CLI: FileStore.create validates,
        # and the file store is "a second drawer, not a degraded path" —
        # the symmetry has to hold in both directions, or a non-CLI caller
        # pushes an over-budget body that nothing would catch. Nothing
        # catches it later any more: read-back validation of filed bodies
        # is gone, so this is the only gate between a malformed record and
        # the network. It runs before ``_gh_ready`` deliberately.
        defects = forge_memory.validate(record)
        if defects:
            raise forge_memory.SchemaError(
                "refusing to store an invalid record: {}".format("; ".join(defects))
            )
        label = self.ORIGIN_LABELS.get(by)
        if label is None:
            raise forge_memory.SchemaError(
                "refusing to file a deferral with no origin: pass by="
                "'human' or 'agent' (got {!r}). Every issue this engine "
                "files carries exactly one origin label, and the engine "
                "cannot infer which — a guess would label a real issue "
                "wrongly.".format(by)
            )
        _gh_ready(self.repo_root)
        body = forge_memory.render(record)
        title = record.fields.get("title", "")
        self._ensure_label(label)
        proc = self._run([
            "issue", "create", "--title", title, "--body", body,
            "--label", label,
        ])
        if proc.returncode != 0:
            _raise_for_gh_failure(proc)
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        number = url.rstrip("/").rsplit("/", 1)[-1]
        return number

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
