#!/usr/bin/env python3
"""Audit every forge-run.py run in a repo: where wall-clock and tokens went,
and which of it repeated work that a run done right the first time would not.

Joins each run dir's receipts and per-role *-events.jsonl (codex thread ids)
to the ~/.codex/sessions rollout files (per-turn timing, model, effort,
tokens, final message). Read-only. Python 3 stdlib only.

usage:
  python3 forge_runs_audit.py <repo> [--codex-home ~/.codex] [--json out.json]
  python3 forge_runs_audit.py --run-dir <one run dir> ...      (single run)

Every dispatch turn is labelled. Necessary work (a run right the first
time): the first build of a task, its first review, the first final review,
doc-sync. Everything else is a repeat, and lands in the waste ledger:

  validation retry (cold|resumed)  reviewer re-dispatched because its verdict
                                   failed validation (no code changed)
  acceptance-failure rework        worker re-run because its own acceptance
                                   commands failed (no review in between)
  review rework / re-review        worker fixing reviewer findings, and the
                                   re-review that checks the fix
  final fix / final re-review      the same cycle at the final review
  cold respawn                     a role restarted on a new session where a
                                   resume was expected (re-invocation after a
                                   halt or crash, or a failed resume)
  after aborted turn               re-dispatch following an aborted/killed turn
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

ATTEMPT_RE = re.compile(r"^task-(\d+)-attempt-(\d+)\.json$")
UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
NECESSARY = {"build", "first review", "first final review", "doc-sync"}
LEDGER = [
    ("validation retry (cold)", "Validation retries — cold (re-review blind)"),
    ("validation retry (resumed)", "Validation retries — resumed"),
    ("acceptance-failure rework", "Rework after own acceptance failed"),
    ("review rework", "Task rework for reviewer findings"),
    ("re-review", "Task re-reviews of a rework"),
    ("final fix", "Final-review fixes"),
    ("final re-review", "Final re-reviews of a fix"),
    ("cold respawn", "Cold respawns (restart / failed resume)"),
    ("after aborted turn", "Re-dispatch after an aborted turn"),
]
RETRY_DEFECT_KINDS = [
    ("unbacked violated", re.compile(r"'violated' coverage|names no backing finding|not in this verdict|backing finding", re.I)),
    ("contract_ref not citable", re.compile(r"is not a citable ref", re.I)),
    ("unknown checklist id", re.compile(r"unknown checklist id", re.I)),
    ("missing coverage", re.compile(r"missing coverage", re.I)),
    ("duplicate id", re.compile(r"duplicate", re.I)),
    ("empty evidence", re.compile(r"empty evidence", re.I)),
    ("location", re.compile(r"location", re.I)),
    ("finding on non-violated", re.compile(r"finding.*non-violated|on a .* entry", re.I)),
]


def parse_ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_dur(seconds):
    seconds = int(round(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "{}h{:02d}m".format(h, m) if h else "{}m{:02d}s".format(m, s)


def fmt_tok(n):
    n = n or 0
    if n >= 1_000_000:
        return "{:.1f}M".format(n / 1e6)
    if n >= 1000:
        return "{:.0f}k".format(n / 1e3)
    return str(n)


def last_verdict(text):
    if not text:
        return None
    dec = json.JSONDecoder()
    for i in range(len(text) - 1, -1, -1):
        if text[i] != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict) and "verdict" in obj:
            return obj
    return None


def verdict_summary(v):
    if v is None:
        return None
    if v.get("verdict") == "pass":
        return {"verdict": "pass", "findings": 0}
    fs = v.get("findings") or []
    return {"verdict": "findings", "findings": len(fs),
            "contract_breaking": sum(1 for f in fs if isinstance(f, dict)
                                     and f.get("impact") == "contract-breaking")}


# --------------------------------------------------------------- rollouts

def index_rollouts(codex_home):
    idx = collections.defaultdict(list)
    for p in glob.glob(os.path.join(codex_home, "sessions", "**", "rollout-*.jsonl"), recursive=True):
        m = UUID_RE.search(p)
        if m:
            idx[m.group(1)].append(p)
    return idx


def read_turns(paths):
    """Turns of one codex thread. Tokens per turn = sum of each model call's
    last_token_usage, skipping repeated token_count events (same running
    total), so a resumed thread's cumulative totals are never double-counted."""
    turns, order, ctx = {}, [], {}
    pending = {}  # call_id -> (start ts, command text, turn index)
    last_ts, last_total, bad = None, None, 0
    for path in sorted(paths):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if d.get("timestamp"):
                    last_ts = parse_ts(d["timestamp"])
                p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                t, pt = d.get("type"), p.get("type")
                if t == "turn_context":
                    ctx[p.get("turn_id")] = (p.get("model"), p.get("effort"))
                    continue
                if t == "response_item" and isinstance(pt, str) and order:
                    cid = p.get("call_id")
                    if pt.endswith("_call") and cid:
                        text = p.get("input") or p.get("arguments") or json.dumps(p.get("action") or "")
                        pending[cid] = (last_ts, "{} {}".format(p.get("name") or "", text), order[-1])
                    elif pt.endswith("_call_output") and cid in pending:
                        start, text, tidx = pending.pop(cid)
                        turns[tidx]["tools"].append({
                            "start": start, "end": last_ts,
                            "seconds": (last_ts - start).total_seconds(),
                            "cmd": text, "emulated": EMULATION_RE.search(_output_text(p)) is not None})
                    continue
                if t != "event_msg":
                    continue
                tid = p.get("turn_id")
                if pt == "task_started":
                    turns[tid] = {"start": last_ts, "end": None, "duration": None,
                                  "state": "unfinished", "last_message": None,
                                  "input": 0, "cached": 0, "output": 0, "reasoning": 0,
                                  "tools": []}
                    order.append(tid)
                elif pt == "token_count" and order:
                    info = p.get("info") or {}
                    total = (info.get("total_token_usage") or {}).get("total_tokens")
                    if total is not None and total == last_total:
                        continue
                    last_total = total
                    lu = info.get("last_token_usage") or {}
                    cur = turns[order[-1]]
                    cur["input"] += lu.get("input_tokens", 0) or 0
                    cur["cached"] += lu.get("cached_input_tokens", 0) or 0
                    cur["output"] += lu.get("output_tokens", 0) or 0
                    cur["reasoning"] += lu.get("reasoning_output_tokens", 0) or 0
                elif pt in ("task_complete", "turn_aborted") and tid in turns:
                    cur = turns[tid]
                    cur["state"] = "complete" if pt == "task_complete" else "aborted"
                    cur["end"] = last_ts
                    if p.get("duration_ms") is not None:
                        cur["duration"] = p["duration_ms"] / 1000.0
                    cur["last_message"] = p.get("last_agent_message")
                if order and turns[order[-1]]["state"] == "unfinished":
                    turns[order[-1]]["end_seen"] = last_ts
    out = []
    for tid in order:
        t = turns[tid]
        t["model"], t["effort"] = ctx.get(tid, (None, None))
        if t["end"] is None:
            t["end"] = t.get("end_seen") or t["start"]
        if t["duration"] is None and t["start"] and t["end"]:
            t["duration"] = (t["end"] - t["start"]).total_seconds()
        classify_tools(t["tools"])
        t["tool_seconds"] = min(union_seconds(t["tools"]), t["duration"] or 0)
        out.append(t)
    return out, bad


# --------------------------------------------------------------- tool calls

EMULATION_RE = re.compile(r"requested image's platform \(\S+\) does not match the detected host platform", re.I)
# Order matters: the first matching category wins, and "docker" is checked
# first so a test run inside a container counts as container time.
TOOL_CATEGORIES = [
    ("docker", re.compile(r"\bdocker\b|\bdocker-compose\b|\bpodman\b|\bcompose (run|up|exec|build)\b")),
    ("test", re.compile(r"\bpytest\b|\bunittest\b|\bvitest\b|\bjest\b|\bplaywright\b|\bnode --test\b|"
                        r"\bnpm (run )?test\b|\bpnpm (run )?test\b|\byarn test\b|\bgo test\b|\bcargo test\b|\bmake test\b")),
    ("build/install", re.compile(r"\bnpm (ci|install|run build)\b|\bpnpm (install|build)\b|\bpip (install|sync)\b|"
                                 r"\buv (sync|pip)\b|\bpoetry install\b|\bvite build\b|\btsc\b|\bmake\b")),
    ("read/search", re.compile(r"\b(rg|grep|sed|cat|ls|find|head|tail|wc|nl|git (diff|log|status|show|ls-files|rev-parse))\b")),
    ("edit", re.compile(r"apply_patch|\*\*\* Begin Patch")),
]
POLL_RE = re.compile(r"^\s*wait\b|write_stdin|\bpoll|cmd\s*:\s*\"\"", re.I)


def _output_text(p):
    out = p.get("output")
    if isinstance(out, list):
        return " ".join(str(o.get("text", "")) for o in out if isinstance(o, dict))
    return str(out or "")


def classify_tools(tools):
    """Category per tool call from its command text. A call with no
    recognizable command that only polls a running process (a long test or
    container run yielding output) inherits the previous call's category."""
    prev = "other"
    for c in tools:
        cat = None
        for name, rx in TOOL_CATEGORIES:
            if rx.search(c["cmd"]):
                cat = name
                break
        c["inherited"] = False
        if cat is None:
            if POLL_RE.search(c["cmd"]):
                cat, c["inherited"] = prev, True
            else:
                cat = "other"
        c["category"] = cat
        c["docker_test"] = cat == "docker" and TOOL_CATEGORIES[1][1].search(c["cmd"]) is not None
        prev = cat


def union_seconds(tools):
    spans = sorted((c["start"], c["end"]) for c in tools if c["start"] and c["end"])
    total, cur_s, cur_e = 0.0, None, None
    for s, e in spans:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


def cmd_snippet(text, n=90):
    m = re.search(r"cmd\s*:\s*\"((?:[^\"\\]|\\.)*)\"", text)
    s = m.group(1) if m else text
    s = re.sub(r"\s+", " ", s.replace("\\n", " ")).strip()
    return s[:n] + ("…" if len(s) > n else "")


# --------------------------------------------------------------- run dir

def role_threads(run_dir):
    roles = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "*-events.jsonl"))):
        role = os.path.basename(path)[: -len("-events.jsonl")]
        tids = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "thread.started":
                    tid = ev.get("thread_id")
                    if tid and tid not in tids:
                        tids.append(tid)
        roles[role] = tids
    return roles


def read_receipts(run_dir):
    rec = collections.defaultdict(dict)
    for name in os.listdir(run_dir):
        m = ATTEMPT_RE.match(name)
        if m:
            try:
                with open(os.path.join(run_dir, name), encoding="utf-8") as f:
                    rec[int(m.group(1))][int(m.group(2))] = json.load(f)
            except (OSError, ValueError):
                pass
    return rec


def retry_defects(run_dir):
    """Defect lines from every retry prompt file the run wrote."""
    out = []
    for path in glob.glob(os.path.join(run_dir, "*-coverage-retry.md")) + \
            glob.glob(os.path.join(run_dir, "*-retry-defects.md")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        tail = text.split("Coverage retry", 1)[-1] if "Coverage retry" in text else text
        for line in tail.splitlines():
            if line.startswith("- "):
                out.append((os.path.basename(path), line[2:].strip()))
    return out


def defect_kind(line):
    for kind, rx in RETRY_DEFECT_KINDS:
        if rx.search(line):
            return kind
    return "other"


def unit_of(role):
    m = re.match(r"^task-(\d+)-(worker|reviewer)$", role)
    if m:
        return "task-" + m.group(1), m.group(2)
    if role == "final-reviewer":
        return "final", "reviewer"
    if role == "final-fixer":
        return "final", "worker"
    return role, "other"


def label_unit(turns, is_final):
    """Label one unit's turns (one task, or the final review) in time order."""
    prev = None
    seen_worker_threads, seen_reviewer = set(), False
    for t in turns:
        kind = t["kind"]
        if kind == "worker":
            if is_final:
                lab = "final fix"
            elif not seen_worker_threads:
                lab = "build"
            elif t["thread"] not in seen_worker_threads:
                lab = "cold respawn"
            elif prev is not None and prev["kind"] == "worker":
                lab = "acceptance-failure rework"
            else:
                lab = "review rework"
            seen_worker_threads.add(t["thread"])
        elif kind == "reviewer":
            if prev is not None and prev["kind"] == "reviewer":
                if prev["state"] != "complete":
                    lab = "after aborted turn"
                elif t["thread"] == prev["thread"]:
                    lab = "validation retry (resumed)"
                else:
                    lab = "validation retry (cold)"
            elif not seen_reviewer:
                lab = "first final review" if is_final else "first review"
            else:
                lab = "final re-review" if is_final else "re-review"
            seen_reviewer = True
        else:
            lab = "doc-sync" if t["role"] == "doc-sync" else t["role"]
        if t["state"] != "complete" and lab in NECESSARY:
            lab = "after aborted turn" if prev is not None else lab
        t["label"] = lab
        prev = t


def analyze_run(run_dir, index):
    roles = role_threads(run_dir)
    receipts = read_receipts(run_dir)
    run_json = {}
    rj = os.path.join(run_dir, "run.json")
    if os.path.exists(rj):
        try:
            run_json = json.load(open(rj, encoding="utf-8"))
        except ValueError:
            pass
    turns, missing, bad = [], [], 0
    for role, tids in roles.items():
        unit, kind = unit_of(role)
        for tid in tids:
            paths = index.get(tid)
            if not paths:
                missing.append(role)
                continue
            ts, b = read_turns(paths)
            bad += b
            for i, t in enumerate(ts):
                t.update(role=role, thread=tid, unit=unit, kind=kind, lap=i + 1)
                turns.append(t)
    turns.sort(key=lambda t: t["start"])
    by_unit = collections.defaultdict(list)
    for t in turns:
        by_unit[t["unit"]].append(t)
    for unit, ts in by_unit.items():
        label_unit(ts, unit == "final")
    for t in turns:
        t["verdict"] = verdict_summary(last_verdict(t["last_message"])) if t["kind"] == "reviewer" else None

    tasks = {}
    for n, atts in receipts.items():
        first = atts[min(atts)]
        last = atts[max(atts)]
        tasks[n] = {"title": first.get("title"), "tier": first.get("tier"),
                    "model": first.get("model"), "effort": first.get("effort"),
                    "attempts": len(atts), "status": last.get("status"),
                    "halt_reason": last.get("halt_reason")}
    gaps = []
    end = None
    for t in turns:
        if end is not None:
            g = (t["start"] - end).total_seconds()
            if g >= 120:
                gaps.append({"seconds": g, "before": t["role"]})
        end = max(end, t["end"]) if end else t["end"]
    span = (max(t["end"] for t in turns) - turns[0]["start"]).total_seconds() if turns else 0
    return {"run": os.path.basename(run_dir.rstrip("/")), "path": run_dir,
            "plan": run_json.get("plan"), "status": run_json.get("status"),
            "started_at": run_json.get("started_at"), "span": span,
            "turns": turns, "tasks": tasks, "gaps": gaps, "missing": missing,
            "bad_lines": bad, "retry_defects": retry_defects(run_dir)}


# --------------------------------------------------------------- report

def agg(turns):
    a = {"n": 0, "seconds": 0.0, "input": 0, "cached": 0, "output": 0, "reasoning": 0}
    for t in turns:
        a["n"] += 1
        a["seconds"] += t["duration"] or 0
        for k in ("input", "cached", "output", "reasoning"):
            a[k] += t[k] or 0
    return a


def line_agg(name, a, total_s=None):
    pct = " ({:>3.0f}%)".format(100 * a["seconds"] / total_s) if total_s else ""
    return "  {:<44} {:>4} turns {:>8}{}  in {:>7} (cached {:>3.0f}%)  out {:>6}".format(
        name, a["n"], fmt_dur(a["seconds"]), pct, fmt_tok(a["input"]),
        100 * a["cached"] / a["input"] if a["input"] else 0, fmt_tok(a["output"]))


def report(runs):
    all_turns = [t for r in runs for t in r["turns"]]
    if not all_turns:
        print("No dispatch turns found. Are the codex session logs on this machine?")
        return
    busy = sum(t["duration"] or 0 for t in all_turns)
    nec = [t for t in all_turns if t["label"] in NECESSARY]
    rep = [t for t in all_turns if t["label"] not in NECESSARY]
    print("=" * 100)
    print("REPO SUMMARY — {} runs, {} dispatch turns, {} busy (sum of turns), {} wall span (sum of runs)".format(
        len(runs), len(all_turns), fmt_dur(busy), fmt_dur(sum(r["span"] for r in runs))))
    print("=" * 100)
    print(line_agg("Necessary (right-first-time) work", agg(nec), busy))
    print(line_agg("Repeated work", agg(rep), busy))

    print("\n-- Waste ledger (repeated work, ranked by time) " + "-" * 50)
    rows = []
    for key, name in LEDGER:
        ts = [t for t in rep if t["label"] == key]
        if ts:
            rows.append((agg(ts), name))
    other = [t for t in rep if t["label"] not in {k for k, _ in LEDGER}]
    if other:
        rows.append((agg(other), "Other ({})".format(", ".join(sorted({t['label'] for t in other})))))
    for a, name in sorted(rows, key=lambda r: -r[0]["seconds"]):
        print(line_agg(name, a, busy))
    idle = sum(g["seconds"] for r in runs for g in r["gaps"])
    print("  {:<44} {:>4} gaps  {:>8}   (halts waiting on a human, acceptance runs, runner overhead)".format(
        "Idle gaps >= 2m", sum(len(r["gaps"]) for r in runs), fmt_dur(idle)))

    print("\n-- Where necessary work goes (by role) " + "-" * 59)
    for lab in ("build", "first review", "first final review", "doc-sync"):
        ts = [t for t in nec if t["label"] == lab]
        if ts:
            a = agg(ts)
            print(line_agg("{} (avg {} each)".format(lab, fmt_dur(a["seconds"] / a["n"])), a, busy))

    report_tools(runs, all_turns)

    print("\n-- Model / effort mix (all turns) " + "-" * 64)
    by_me = collections.defaultdict(list)
    for t in all_turns:
        by_me["{}/{}".format(t["model"], t["effort"])].append(t)
    for k, ts in sorted(by_me.items(), key=lambda kv: -agg(kv[1])["seconds"]):
        print(line_agg(k, agg(ts), busy))

    print("\n-- Tier fit: tasks that passed first review with no rework " + "-" * 38)
    clean = collections.defaultdict(list)
    for r in runs:
        units = collections.defaultdict(list)
        for t in r["turns"]:
            units[t["unit"]].append(t)
        for n, info in r["tasks"].items():
            ts = units.get("task-{}".format(n), [])
            labels = [t["label"] for t in ts]
            fr = [t for t in ts if t["label"] == "first review"]
            if fr and fr[0]["verdict"] and fr[0]["verdict"]["verdict"] == "pass" and set(labels) <= NECESSARY:
                clean[info.get("tier")].append(sum(t["duration"] or 0 for t in ts))
    total_tasks = sum(len(r["tasks"]) for r in runs)
    for tier, secs in sorted(clean.items(), key=lambda kv: str(kv[0])):
        print("  {:<10} {:>3} clean tasks, {} total ({} avg)".format(
            str(tier), len(secs), fmt_dur(sum(secs)), fmt_dur(sum(secs) / len(secs))))
    print("  ({} of {} tasks clean.) Clean complex-tier tasks are candidates for standard tier.".format(
        sum(len(v) for v in clean.values()), total_tasks))

    print("\n-- Validation retry causes (from retry prompt files) " + "-" * 46)
    kinds = collections.Counter()
    files = collections.Counter()
    for r in runs:
        seen = set()
        for fname, line in r["retry_defects"]:
            kinds[defect_kind(line)] += 1
            seen.add(fname)
        files[r["run"]] += len(seen)
    print("  {} retry files across {} runs (one file per task per run; repeats overwrite)".format(
        sum(files.values()), sum(1 for v in files.values() if v)))
    for k, c in kinds.most_common():
        print("  {:<28} {:>4} defect lines".format(k, c))
    drift = []
    for r in runs:
        for i, t in enumerate(r["turns"]):
            if t["label"].startswith("validation retry"):
                prev = [p for p in r["turns"][:i] if p["unit"] == t["unit"] and p["kind"] == "reviewer"]
                if prev and prev[-1]["verdict"] and t["verdict"]:
                    a, b = prev[-1]["verdict"]["findings"], t["verdict"]["findings"]
                    if a != b:
                        drift.append((r["run"], t["unit"], t["label"], a, b))
    if drift:
        print("  Findings changed across a retry (the retry replaced the first verdict):")
        for run, unit, lab, a, b in drift:
            print("    {} {} [{}]: {} -> {} findings".format(run, unit, lab, a, b))

    print("\n-- Context weight: average tokens per turn by label " + "-" * 47)
    by_lab = collections.defaultdict(list)
    for t in all_turns:
        by_lab[t["label"]].append(t)
    print("  {:<30} {:>5} {:>10} {:>8} {:>10}".format("label", "turns", "avg in", "cached", "avg out"))
    for lab, ts in sorted(by_lab.items(), key=lambda kv: -agg(kv[1])["input"]):
        a = agg(ts)
        print("  {:<30} {:>5} {:>10} {:>7.0f}% {:>10}".format(
            lab, a["n"], fmt_tok(int(a["input"] / a["n"])),
            100 * a["cached"] / a["input"] if a["input"] else 0, fmt_tok(int(a["output"] / a["n"]))))

    print("\n" + "=" * 100)
    print("PER RUN")
    print("=" * 100)
    for r in sorted(runs, key=lambda r: r["started_at"] or ""):
        ts = r["turns"]
        a_n = agg([t for t in ts if t["label"] in NECESSARY])
        a_r = agg([t for t in ts if t["label"] not in NECESSARY])
        print("\n{}  [{}]  started {}  span {}  necessary {}  repeated {}".format(
            r["run"], r["status"], r["started_at"], fmt_dur(r["span"]),
            fmt_dur(a_n["seconds"]), fmt_dur(a_r["seconds"])))
        if r["plan"]:
            print("  plan: {}".format(r["plan"]))
        for n, info in sorted(r["tasks"].items()):
            print("  task {} [{} {}/{}] {} attempt(s), {}{}  {}".format(
                n, info["tier"], info["model"], info["effort"], info["attempts"], info["status"],
                " halt=" + str(info["halt_reason"]) if info["halt_reason"] else "", (info["title"] or "")[:50]))
        tl = sum(t["tool_seconds"] for t in ts)
        dk = sum(c["seconds"] for t in ts for c in t["tools"] if c["category"] == "docker")
        em = sum(c["seconds"] for t in ts for c in t["tools"] if c["emulated"])
        print("  tool time {} of {} turn time; containers {}{}".format(
            fmt_dur(tl), fmt_dur(sum(t["duration"] or 0 for t in ts)), fmt_dur(dk),
            ", emulated {}".format(fmt_dur(em)) if em else ""))
        cnt = collections.Counter(t["label"] for t in ts if t["label"] not in NECESSARY)
        if cnt:
            print("  repeats: " + ", ".join("{} x{}".format(k, v) for k, v in cnt.most_common()))
        if r["gaps"]:
            print("  idle gaps >= 2m: " + ", ".join("{} before {}".format(fmt_dur(g["seconds"]), g["before"]) for g in r["gaps"]))
        if r["missing"]:
            print("  WARNING: {} thread(s) with no rollout file — times incomplete ({})".format(
                len(r["missing"]), ", ".join(sorted(set(r["missing"])))))


def report_tools(runs, all_turns):
    """Split turn time into model time and tool time, and tool time by what
    the commands did — containers, tests, builds, reads. Tool time per turn is
    the union of its tool calls' wall-clock spans (call -> output), so
    overlapping calls never count twice."""
    calls = [(r, t, c) for r in runs for t in r["turns"] for c in t["tools"]]
    busy = sum(t["duration"] or 0 for t in all_turns)
    tool = sum(t["tool_seconds"] for t in all_turns)
    print("\n-- Model time vs tool time " + "-" * 71)
    if not calls:
        print("  no tool calls found in the session logs")
        return
    print("  turn time {}  =  tools {} ({:.0f}%)  +  model/other {} ({:.0f}%)   [{} tool calls]".format(
        fmt_dur(busy), fmt_dur(tool), 100 * tool / busy if busy else 0,
        fmt_dur(busy - tool), 100 * (busy - tool) / busy if busy else 0, len(calls)))
    print("\n  {:<30} {:>5} {:>9} {:>8} {:>10} {:>9}".format(
        "by label", "turns", "time", "tools %", "container", "cont. %"))
    by_lab = collections.defaultdict(list)
    for t in all_turns:
        by_lab[t["label"]].append(t)
    for lab, ts in sorted(by_lab.items(), key=lambda kv: -sum(t["duration"] or 0 for t in kv[1])):
        dur = sum(t["duration"] or 0 for t in ts)
        tl = sum(t["tool_seconds"] for t in ts)
        dk = sum(c["seconds"] for t in ts for c in t["tools"] if c["category"] == "docker")
        print("  {:<30} {:>5} {:>9} {:>7.0f}% {:>10} {:>8.0f}%".format(
            lab, len(ts), fmt_dur(dur), 100 * tl / dur if dur else 0, fmt_dur(dk), 100 * dk / dur if dur else 0))

    print("\n  {:<16} {:>6} {:>9} {:>8} {:>8}   (sum of call spans; parallel calls may overlap)".format(
        "by category", "calls", "time", "avg", "max"))
    by_cat = collections.defaultdict(list)
    for _, _, c in calls:
        by_cat[c["category"]].append(c["seconds"])
    for cat, secs in sorted(by_cat.items(), key=lambda kv: -sum(kv[1])):
        print("  {:<16} {:>6} {:>9} {:>8} {:>8}".format(
            cat, len(secs), fmt_dur(sum(secs)), fmt_dur(sum(secs) / len(secs)), fmt_dur(max(secs))))

    inh = [c for _, _, c in calls if c["inherited"]]
    print("  {} calls ({}) were waits on a running command and took the previous call's category".format(
        len(inh), fmt_dur(sum(c["seconds"] for c in inh))))

    dk = [c for _, _, c in calls if c["category"] == "docker"]
    print("\n  Containers: {} calls, {} total".format(len(dk), fmt_dur(sum(c["seconds"] for c in dk))))
    dt = [c for c in dk if c["docker_test"]]
    if dt:
        print("    tests run inside a container: {} calls, {}".format(len(dt), fmt_dur(sum(c["seconds"] for c in dt))))
    em = [c for _, _, c in calls if c["emulated"]]
    print("    calls whose output shows amd64-on-arm64 emulation: {} calls, {}".format(
        len(em), fmt_dur(sum(c["seconds"] for c in em))))
    per_run = collections.Counter()
    for r, _, c in calls:
        if c["category"] == "docker":
            per_run[r["run"]] += c["seconds"]
    for run, s in per_run.most_common():
        print("    {:<34} {}".format(run, fmt_dur(s)))

    print("\n  Slowest 15 tool calls:")
    for r, t, c in sorted(calls, key=lambda x: -x[2]["seconds"])[:15]:
        print("    {:>8}  {:<14}{} {} {} [{}]  {}".format(
            fmt_dur(c["seconds"]), c["category"], " EMU" if c["emulated"] else "    ",
            r["run"], t["role"], t["label"], cmd_snippet(c["cmd"], 70)))


def find_runs(repo):
    base = os.path.join(repo, ".forge", "runs")
    if not os.path.isdir(base):
        sys.exit("error: no .forge/runs under {}".format(repo))
    out, skipped = [], []
    for d in sorted(os.listdir(base)):
        p = os.path.join(base, d)
        if not os.path.isdir(p):
            continue
        if glob.glob(os.path.join(p, "*-events.jsonl")):
            out.append(p)
        else:
            skipped.append(d)
    return out, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--run-dir")
    ap.add_argument("--codex-home", default=os.path.expanduser("~/.codex"))
    ap.add_argument("--json", help="also write the full per-turn data to this path")
    args = ap.parse_args()
    if not args.repo and not args.run_dir:
        ap.error("give a repo path or --run-dir")
    if not os.path.isdir(os.path.join(args.codex_home, "sessions")):
        sys.exit("error: no sessions dir under --codex-home {}".format(args.codex_home))
    if args.run_dir:
        dirs, skipped = [args.run_dir], []
    else:
        dirs, skipped = find_runs(args.repo)
    print("indexing codex session logs ...", file=sys.stderr)
    index = index_rollouts(args.codex_home)
    runs = []
    for d in dirs:
        print("  analyzing {}".format(os.path.basename(d)), file=sys.stderr)
        runs.append(analyze_run(d, index))
    if skipped:
        print("Skipped (no *-events.jsonl — not a forge-run.py run, or pre-0.12): " + ", ".join(skipped))
    report(runs)
    if args.json:
        def enc(o):
            if isinstance(o, datetime.datetime):
                return o.isoformat()
            raise TypeError(type(o))
        for r in runs:
            for t in r["turns"]:
                t.pop("last_message", None)
                t.pop("end_seen", None)
                for c in t["tools"]:
                    c["cmd"] = cmd_snippet(c["cmd"], 200)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(runs, f, default=enc, indent=1)
        print("\nper-turn data written to {}".format(args.json))


if __name__ == "__main__":
    main()
