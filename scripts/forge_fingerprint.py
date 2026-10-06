"""forge_fingerprint — CLI over forge_git's repository fingerprint, for the
Claude dispatch loop (the Codex runner calls forge_git in-process).

  forge_fingerprint.py snapshot
      Print the fingerprint of the repository in the current directory as one
      JSON object (keys: tree, index, head, branch).

  forge_fingerprint.py verify <json-or-path>
      Compare the repository now to a recorded fingerprint, given inline as
      JSON or as a path to a file holding it. Exit 0 when equal; exit 2
      printing one line per change when not; exit 1 on a git failure or a
      malformed fingerprint (the cause on stderr).

  forge_fingerprint.py freeze <json-or-path> --ref <ref-name>
      The reviewer-wrote halt path. Point the recorded branch back at the
      recorded HEAD sha and re-attach HEAD (forge_git.restore_refs), then park
      the recorded pre-review tree as a commit under --ref, parented on the
      recorded HEAD (forge_git.freeze_tree), returning the working tree to the
      checkpoint. Prints the freeze sha, or `none` when the recorded tree
      equals the recorded HEAD's tree (nothing to freeze; the working tree is
      still returned to HEAD). Exit 1 naming the git command on a failure.

Imported helpers live in forge_git; stdlib only.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import forge_git  # noqa: E402

KEYS = ("tree", "index", "head", "branch")

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REF = re.compile(r"refs/[A-Za-z0-9._/-]+\Z")
_BRANCH = re.compile(r"refs/heads/[A-Za-z0-9._/-]+\Z")


def _validate_fingerprint(fp, origin):
    """Every field's shape, before any value can reach git (parsers-fail-loud:
    a malformed fingerprint is a usage error naming the field, never a
    mismatch and never a traceback)."""
    for k in ("tree", "index", "head"):
        v = fp[k]
        if not isinstance(v, str) or not _SHA.match(v):
            raise ValueError(
                "fingerprint {}: field {} must be a 40-hex sha, got {!r}".format(
                    origin, k, v)
            )
    b = fp["branch"]
    if b is not None and (not isinstance(b, str) or not _BRANCH.match(b)
                          or ".." in b):
        raise ValueError(
            "fingerprint {}: field branch must be null or refs/heads/<name>, "
            "got {!r}".format(origin, b)
        )


def _validate_ref(ref, branch):
    if (not _REF.match(ref) or ".." in ref or ref.startswith("-")
            or ref.startswith("refs/heads/") or ref == branch):
        raise ValueError(
            "--ref {!r} is not an allowed freeze ref: it must match "
            "refs/<name> with no '..', must not be under refs/heads/ and must "
            "not be the recorded branch".format(ref)
        )


def _load_fingerprint(arg):
    if os.path.isfile(arg):
        try:
            with open(arg, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise ValueError("cannot read fingerprint file {}: {}".format(arg, e))
        origin = "file " + arg
    else:
        text, origin = arg, "argument"
    try:
        fp = json.loads(text)
    except ValueError as e:
        raise ValueError("fingerprint {} is not valid JSON: {}".format(origin, e))
    if not isinstance(fp, dict):
        raise ValueError("fingerprint {} is not a JSON object".format(origin))
    missing = [k for k in KEYS if k not in fp]
    if missing:
        raise ValueError(
            "fingerprint {} is missing key(s): {}".format(origin, ", ".join(missing))
        )
    _validate_fingerprint(fp, origin)
    return fp


def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_fingerprint.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="print the repository fingerprint as JSON")
    v = sub.add_parser("verify", help="compare the repository to a fingerprint")
    v.add_argument("fingerprint", help="fingerprint JSON, or a path to a file holding it")
    f = sub.add_parser("freeze", help="restore refs and freeze the recorded tree")
    f.add_argument("fingerprint", help="fingerprint JSON, or a path to a file holding it")
    f.add_argument("--ref", required=True, help="ref name to park the freeze commit under")
    args = parser.parse_args(argv)
    cwd = os.getcwd()
    try:
        if args.cmd == "snapshot":
            print(json.dumps(forge_git.repo_fingerprint(cwd)))
            return 0
        before = _load_fingerprint(args.fingerprint)
        if args.cmd == "freeze":
            _validate_ref(args.ref, before["branch"])
            sha = forge_git.freeze_checkpoint(cwd, before, args.ref)
            print("none" if sha is None else sha)
            return 0
        changes = forge_git.fingerprint_diff(
            cwd, before, forge_git.repo_fingerprint(cwd)
        )
    except (forge_git.FingerprintError, ValueError, RuntimeError) as e:
        print("forge_fingerprint: {}".format(e), file=sys.stderr)
        return 1
    if changes:
        for line in changes:
            print(line)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
