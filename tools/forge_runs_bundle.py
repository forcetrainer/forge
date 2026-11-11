#!/usr/bin/env python3
"""Bundle forge run records into one small zip for analysis on another machine.

For each repo it collects, from ``.forge/runs/<run>/``: run.json, every
attempt receipt, review packets, briefs, resume and retry prompts, last
messages and widening records. It leaves out the Codex event streams
(``*-events.jsonl``) and live logs (``*-live.log``), which are the bulk of a
run directory, and any other file over the size cap.

Also collected per repo: each run's plan and spec files (when they sit inside
the repo), ``.forge/audits/`` JSON written by forge_runs_audit.py, and a
``git log --stat`` of the repo since its earliest bundled run.

Every file seen, bundled or skipped, is listed with its size and modification
time in ``MANIFEST.json`` inside the zip. Read-only on the repos. Python 3
stdlib only.

usage:
  python3 forge_runs_bundle.py <repo> [<repo> ...] [--run NAME ...]
        [--audits DIR ...] [--out bundle.zip] [--max-file-kb 512]

  --run NAME      only runs with this directory name (repeatable; default all)
  --audits DIR    an extra directory of audit JSON to include (repeatable),
                  e.g. the forge checkout's .forge/audits/2026-11-10

The zip quotes the bundled repos' plans, diffs and review findings; treat it
as those repos' data.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import zipfile

SKIP_SUFFIXES = ("-events.jsonl", "-live.log")


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class Bundle:
    def __init__(self, zf, max_bytes):
        self.zf = zf
        self.max_bytes = max_bytes
        self.manifest = []
        self.bundled_bytes = 0
        self.skipped_bytes = 0

    def add_file(self, path, arcname, capped=True):
        st = os.stat(path)
        entry = {"path": arcname, "bytes": st.st_size, "mtime": iso(st.st_mtime)}
        name = os.path.basename(path)
        if name.endswith(SKIP_SUFFIXES):
            entry["skipped"] = "event stream or live log"
        elif capped and st.st_size > self.max_bytes:
            entry["skipped"] = "over the size cap"
        if "skipped" in entry:
            self.skipped_bytes += st.st_size
        else:
            self.zf.write(path, arcname)
            self.bundled_bytes += st.st_size
        self.manifest.append(entry)

    def add_text(self, arcname, text):
        self.zf.writestr(arcname, text)
        self.manifest.append({"path": arcname, "bytes": len(text.encode()),
                              "generated": True})

    def add_tree(self, root, arc_root, only_json=False):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                if only_json and not name.endswith(".json"):
                    continue
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                rel = os.path.relpath(path, root)
                self.add_file(path, "/".join([arc_root] + rel.split(os.sep)),
                              capped=not only_json)


def referenced_docs(run_dir, repo):
    """Plan and spec paths named by a run's run.json that sit inside ``repo``."""
    path = os.path.join(run_dir, "run.json")
    try:
        with open(path) as f:
            run = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(run, dict):
        return []
    named = [run.get("plan"), run.get("spec")] + list(run.get("specs") or [])
    docs = []
    for p in named:
        if not isinstance(p, str) or not p:
            continue
        real = os.path.realpath(p)
        if real.startswith(repo + os.sep) and os.path.isfile(real):
            docs.append(real)
    return docs


def git_log(repo, since_ts):
    cmd = ["git", "-C", repo, "log", "--date=iso-strict", "--stat",
           "--format=commit %H%nparents %P%ndate %ad%nsubject %s%n",
           "--since=" + iso(since_ts - 86400)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "git log failed: {}\n".format(e)
    if out.returncode != 0:
        return "git log exited {}: {}\n".format(out.returncode, out.stderr.strip())
    return out.stdout


def bundle_repo(bundle, repo, only_runs):
    repo = os.path.realpath(repo)
    name = os.path.basename(repo)
    runs_root = os.path.join(repo, ".forge", "runs")
    if not os.path.isdir(runs_root):
        sys.exit("{}: no .forge/runs directory".format(repo))
    runs = sorted(d for d in os.listdir(runs_root)
                  if os.path.isdir(os.path.join(runs_root, d)))
    if only_runs:
        missing = sorted(set(only_runs) - set(runs))
        if missing:
            sys.exit("{}: no such run(s): {}".format(repo, ", ".join(missing)))
        runs = [r for r in runs if r in only_runs]
    if not runs:
        sys.exit("{}: no runs to bundle".format(repo))

    docs, earliest = set(), None
    for run in runs:
        run_dir = os.path.join(runs_root, run)
        bundle.add_tree(run_dir, "{}/runs/{}".format(name, run))
        docs.update(referenced_docs(run_dir, repo))
        mtimes = [os.stat(os.path.join(run_dir, f)).st_mtime
                  for f in os.listdir(run_dir)
                  if os.path.isfile(os.path.join(run_dir, f))]
        if mtimes:
            earliest = min(mtimes) if earliest is None else min(earliest, min(mtimes))
    for doc in sorted(docs):
        rel = os.path.relpath(doc, repo)
        bundle.add_file(doc, "/".join([name, "docs"] + rel.split(os.sep)))
    audits = os.path.join(repo, ".forge", "audits")
    if os.path.isdir(audits):
        bundle.add_tree(audits, "{}/audits".format(name), only_json=True)
    if earliest is not None:
        bundle.add_text("{}/git-log.txt".format(name), git_log(repo, earliest))
    return len(runs)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repos", nargs="+", metavar="repo")
    ap.add_argument("--run", action="append", default=[], metavar="NAME")
    ap.add_argument("--audits", action="append", default=[], metavar="DIR")
    ap.add_argument("--out", default="forge-runs-bundle.zip")
    ap.add_argument("--max-file-kb", type=int, default=512)
    args = ap.parse_args()

    for d in args.audits:
        if not os.path.isdir(d):
            sys.exit("{}: not a directory".format(d))
    counts = {}
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        bundle = Bundle(zf, args.max_file_kb * 1024)
        for repo in args.repos:
            counts[os.path.basename(os.path.realpath(repo))] = bundle_repo(
                bundle, repo, args.run)
        for i, d in enumerate(args.audits):
            bundle.add_tree(os.path.realpath(d), "audits-{}".format(i + 1),
                            only_json=True)
        skipped = [e for e in bundle.manifest if "skipped" in e]
        zf.writestr("MANIFEST.json", json.dumps({
            "created": iso(datetime.datetime.now().timestamp()),
            "runs_per_repo": counts,
            "max_file_kb": args.max_file_kb,
            "files": bundle.manifest,
        }, indent=1))

    mb = 1024.0 * 1024.0
    for repo, n in counts.items():
        print("{}: {} run(s)".format(repo, n))
    print("bundled {} files, {:.1f} MB before compression".format(
        len(bundle.manifest) - len(skipped), bundle.bundled_bytes / mb))
    print("skipped {} files, {:.1f} MB (event streams, live logs, over the cap)".format(
        len(skipped), bundle.skipped_bytes / mb))
    over = [e for e in skipped if e["skipped"] == "over the size cap"]
    for e in over:
        print("  over the cap: {} ({:.1f} MB)".format(e["path"], e["bytes"] / mb))
    print("{}: {:.1f} MB".format(args.out, os.path.getsize(args.out) / mb))


if __name__ == "__main__":
    main()
