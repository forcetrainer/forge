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

    def list(self, type, **filters):
        raise NotImplementedError

    def retire(self, ref, reason=None):
        raise NotImplementedError


def _render_all(records):
    """Canonical whole-file text for ``records``: each rendered by
    ``forge_memory.render``, separated by exactly one blank line — the same
    shape ``forge_memory.fmt_write`` produces, built here from the reused
    ``render`` rather than a second serializer."""
    if not records:
        return ""
    return "\n".join(forge_memory.render(r).rstrip("\n") for r in records) + "\n"


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
            f.write(_render_all(records))

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

    def list(self, type, **filters):
        if type != self.type:
            raise ValueError(
                "FileStore at {!r} holds {!r} records, got {!r}".format(
                    self.path, self.type, type,
                )
            )
        return [r for r in self._read() if _matches(r, filters)]

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


def _raise_for_gh_failure(proc):
    stderr = (proc.stderr or "").strip()
    if "issue" in stderr.lower() and "disabled" in stderr.lower():
        raise StoreUnavailable(
            "Issues are disabled for this repository. Enable Issues in the "
            "repo settings (Settings > General > Features) and re-run. "
            "(gh said: {})".format(stderr)
        )
    raise StoreUnavailable("gh command failed: {}".format(stderr or proc.returncode))


class GitHubStore(Store):
    """``gh issue create/list/close --json`` backend for ``deferral``
    records. Issue bodies are ``forge_memory.render`` output — the same
    renderer ``FileStore`` uses — and are read back through
    ``forge_memory.parse``."""

    LABEL = "forge:deferral"

    def __init__(self, repo_root):
        self.repo_root = repo_root

    def _run(self, args):
        return subprocess.run(
            ["gh"] + args, cwd=self.repo_root, capture_output=True, text=True,
        )

    def create(self, record):
        _gh_ready(self.repo_root)
        body = forge_memory.render(record)
        title = record.fields.get("title", "")
        follow_up = record.fields.get("follow-up")
        args = ["issue", "create", "--title", title, "--body", body,
                "--label", self.LABEL]
        if follow_up:
            args += ["--label", follow_up]
        proc = self._run(args)
        if proc.returncode != 0:
            _raise_for_gh_failure(proc)
        url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        number = url.rstrip("/").rsplit("/", 1)[-1]
        return number

    def list(self, type, state="all", errors=None, **filters):
        """Every issue's body parsed back through ``forge_memory.parse``,
        each returned ``Record`` carrying its issue number as ``.ref`` (the
        public way a caller — ``fmt``'s open-issue check included —
        recovers which issue a record came from; this is the only
        ``gh issue list`` call site in the codebase).

        Default (``errors=None``): fails loud on the first unparsable
        body, naming the issue, same as every other store failure here.
        Passing a list as ``errors`` opts into collect-all-defects mode
        instead — every unparsable body appends an ``(issue_number,
        message)`` pair to it rather than aborting, so one bad issue never
        hides another, and the records that DO parse are still returned
        (and still subject to ``forge_memory.validate`` by the caller, the
        same as a good record from a bad file would be)."""
        _gh_ready(self.repo_root)
        proc = self._run([
            "issue", "list", "--label", self.LABEL, "--state", state,
            "--json", "number,title,body",
        ])
        if proc.returncode != 0:
            _raise_for_gh_failure(proc)
        data = json.loads(proc.stdout or "[]")
        records = []
        for item in data:
            ref = str(item.get("number"))
            try:
                parsed = forge_memory.parse(item.get("body", ""), type)
            except forge_memory.SchemaError as e:
                if errors is None:
                    raise forge_memory.SchemaError(
                        "issue #{}: {}".format(ref, e)
                    )
                errors.append((ref, str(e)))
                continue
            for r in parsed:
                r.ref = ref
                records.append(r)
        return [r for r in records if _matches(r, filters)]

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
