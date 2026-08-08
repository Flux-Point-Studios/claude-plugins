#!/usr/bin/env python3
"""Lessons: what campaigns established, kept where the next campaign reads.

A sweep's most expensive output is not its surviving findings — it is the
argument that produced them. The panel's objections, and above all the
findings it *killed* and why, are recorded in `runs/<runId>.json` and then
never looked at again. So the next sweep over the same ground re-finds the
same items, spends the same refuter fan-out re-arguing them, and arrives at
the same verdicts, having learned nothing from the run before it. The
measured lesson from the first campaign was that verification dominates
cost; re-buying round one every time is where that cost goes.

This file is where a finding stops being per-run. Rows are appended by
`record-run.py` from the compiled graph's own summary — never written by an
agent — keyed by the node's declared dedupe fields, so two runs finding the
same thing produce the same key.

  memory.py --append --run-id ID    file lessons from a run summary (stdin)
  memory.py --load --tag T          print the seed map for args._seen
  memory.py --list [--tag T]        what is known, for humans

Two disciplines carried over from the ledger, both load-bearing:

  * A malformed line is a hard error, never a skipped row. A memory that
    silently shrinks is worse than no memory, because a sweep seeded from a
    truncated file believes it is standing on more ground than it is.
  * Identity is the thing, not the event: `tag|dedupeKey`, latest state
    wins. Re-finding a lesson appends a new row rather than editing the old
    one, so the history of what was believed when stays readable.

What this deliberately does NOT do: suppress. Seeds are advisory — they
tell a finder what earlier rounds surfaced so it can spend itself on new
ground, and nothing here drops an item a finder reports anyway. A killed
lesson is a prior, not a verdict; the code it was about can change, and a
sweep that silently dropped a re-found item would hide exactly the
regression it was run to catch.
"""
import argparse
import datetime
import json
import os
import sys

MEMORY = os.path.join(".claude", "fluxpoint", "memory.jsonl")
RUNS = os.path.join(".claude", "fluxpoint", "runs")
STATUSES = {"surviving", "killed", "superseded"}
REQUIRED = ("tag", "dedupeKey", "claim", "status", "node", "provenance")


def path_for(root):
    return os.path.join(root, MEMORY)


def read(root):
    """Every filed lesson, oldest first. A malformed line is fatal."""
    p = path_for(root)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"memory: {p}:{i} is not valid JSON: {e}")
    return out


def current(root, tag=None):
    """Latest state per identity, newest wins. Optionally one tag."""
    state = {}
    for r in read(root):
        if tag and r.get("tag") != tag:
            continue
        state[f"{r.get('tag')}|{r.get('dedupeKey')}"] = r
    return list(state.values())


def validate(row):
    """Shallow shape check against LessonV1. Returns a list of findings.

    These rows are machine-produced, so this is a self-check rather than a
    gate on someone's typing — but a producer bug that files half a lesson
    would poison every later sweep, and this is the cheapest place to catch
    one.
    """
    f = []
    for k in REQUIRED:
        if k not in row:
            f.append(f"missing required field '{k}'")
    if row.get("status") not in STATUSES and "status" in row:
        f.append(f"status must be one of {sorted(STATUSES)}, got {row.get('status')!r}")
    claim = row.get("claim")
    if isinstance(claim, str) and len(claim.strip()) < 20:
        f.append("claim is under 20 characters — a lesson nobody can act on")
    prov = row.get("provenance")
    if "provenance" in row and (not isinstance(prov, dict) or not prov.get("runId")):
        f.append("provenance.runId required — a lesson with no run behind it is an assertion")
    return f


def append_from_summary(root, summary, run_id, state_dir=None):
    """File a row per lesson the run emitted. Returns (written, findings).

    The runId has to name a recorded run. Provenance that points at nothing
    is the opening a fabricated summary would use to plant durable lessons,
    and a row here outlives the run that made it.
    """
    rows = (summary or {}).get("memory") or []
    if not rows:
        return [], []
    runs_dir = state_dir or os.path.join(root, RUNS)
    if not os.path.exists(os.path.join(runs_dir, f"{run_id}.json")):
        return [], [
            f"refusing to file {len(rows)} lesson(s): no run artifact for '{run_id}' "
            f"in {runs_dir} — provenance that points at nothing is how a "
            f"fabricated summary would plant durable memory"]
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    known = {f"{r.get('tag')}|{r.get('dedupeKey')}": r for r in read(root)}
    written, findings = [], []
    p = path_for(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            if not isinstance(r, dict):
                findings.append(f"skipped a lesson that is not an object: {r!r:.60}")
                continue
            row = {
                "tag": r.get("tag"),
                "dedupeKey": r.get("dedupeKey"),
                "claim": r.get("claim"),
                "status": r.get("status"),
                "objection": r.get("objection") or "",
                "kills": r.get("kills", 0),
                "node": r.get("node"),
                "provenance": {"runId": run_id},
                "establishedWhen": when,
            }
            bad = validate(row)
            if bad:
                findings.append(
                    f"skipped a malformed lesson from node "
                    f"'{row.get('node')}': {'; '.join(bad)}")
                continue
            prior = known.get(f"{row['tag']}|{row['dedupeKey']}")
            if prior and prior.get("provenance", {}).get("runId") != run_id:
                # Nothing is rewritten: the older row keeps its place in the
                # file and the new one names what it replaced.
                row["supersededBy"] = run_id
            fh.write(json.dumps(row) + "\n")
            written.append(row)
            known[f"{row['tag']}|{row['dedupeKey']}"] = row
    return written, findings


def seed_map(root, tags):
    """{tag: {keys, killed}} for args._seen, one entry per requested tag.

    `keys` is what earlier runs surfaced, in the same shape the compiled
    graph's own seen-list uses. `killed` carries the claim and the objection
    that killed it, which is the part a fresh context cannot reconstruct.
    """
    out = {}
    for tag in tags:
        rows = current(root, tag)
        out[tag] = {
            "keys": [r["dedupeKey"] for r in rows if r.get("dedupeKey")],
            "killed": [
                {"claim": r.get("claim"), "objection": r.get("objection") or ""}
                for r in rows if r.get("status") == "killed"
            ],
        }
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--tag", action="append", default=[],
                    help="tag to load or list; repeatable")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--result", help="run summary JSON file (default stdin)")
    ap.add_argument("--state-dir", help="recorded-runs directory (provenance check)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--append", action="store_true")
    g.add_argument("--load", action="store_true")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.load:
        if not a.tag:
            ap.error("--load requires at least one --tag")
        # Always valid JSON, even when empty: a first sweep has no lessons,
        # which is different from a loader that failed to run.
        print(json.dumps(seed_map(a.root, a.tag)))
        return 0

    if a.list:
        rows = current(a.root, a.tag[0] if a.tag else None)
        if not rows:
            print("memory: no lessons filed yet")
            return 0
        by_tag = {}
        for r in rows:
            by_tag.setdefault(r.get("tag"), []).append(r)
        print(f"memory: {len(rows)} lesson(s) across {len(by_tag)} tag(s)")
        for tag, rs in sorted(by_tag.items()):
            killed = sum(1 for r in rs if r.get("status") == "killed")
            print(f"  [{tag}] {len(rs)} lesson(s), {killed} killed")
            for r in rs[:10]:
                print(f"      ({r.get('status')}) {str(r.get('claim'))[:90]}")
                if r.get("objection"):
                    print(f"          objection: {str(r['objection'])[:80]}")
            if len(rs) > 10:
                print(f"      [{len(rs) - 10} more — read {path_for(a.root)}]")
        return 0

    if not a.run_id:
        ap.error("--append requires --run-id")
    raw = open(a.result, encoding="utf-8").read() if a.result else sys.stdin.read()
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"memory: result is not JSON: {e}", file=sys.stderr)
        return 1
    written, findings = append_from_summary(a.root, summary, a.run_id, a.state_dir)
    for f in findings:
        print(f"memory: {f}", file=sys.stderr)
    if written:
        killed = sum(1 for r in written if r["status"] == "killed")
        print(f"memory: filed {len(written)} lesson(s), {killed} killed")
    else:
        print("memory: no lessons in this run")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
