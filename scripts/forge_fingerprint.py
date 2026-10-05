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

Imported helpers live in forge_git; stdlib only.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import forge_git  # noqa: E402

KEYS = ("tree", "index", "head", "branch")


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
    return fp


def main(argv=None):
    parser = argparse.ArgumentParser(prog="forge_fingerprint.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="print the repository fingerprint as JSON")
    v = sub.add_parser("verify", help="compare the repository to a fingerprint")
    v.add_argument("fingerprint", help="fingerprint JSON, or a path to a file holding it")
    args = parser.parse_args(argv)
    cwd = os.getcwd()
    try:
        if args.cmd == "snapshot":
            print(json.dumps(forge_git.repo_fingerprint(cwd)))
            return 0
        before = _load_fingerprint(args.fingerprint)
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
