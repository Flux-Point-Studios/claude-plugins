#!/usr/bin/env python3
"""Graph-shaped metrics from recorded runs — observe the graph, not the chat.

Every number here is folded deterministically out of artifacts other tools
already write: runs/<runId>.json (record-run.py), memory.jsonl (lessons with
their kill verdicts), inbox.jsonl (what waited on a person). No model reads
a transcript; a rate nobody can recompute from the artifacts is not
reported.

Per campaign it reports: run count and outcome histogram; node totals and
the death/skip rates; agent calls actually spawned vs the compile-time
plan and the runtime's own token meter (runs that predate these fields are
counted and named, never guessed); discovery behavior (rounds, dry-rule vs
ceiling endings, per-worker unique-new tallies) from the structured `data`
carried on provenance rows; reduce compression (items in -> items out);
panel kill rate from filed lessons; attestation tallies; and how many
inbox items each campaign raised.

A malformed run artifact is reported by name and skipped — what a metrics
pass says must not depend on which artifacts happened to parse, and a
summary that quietly dropped one would read as a cleaner history than the
repo has.

  metrics.py [--root .] [--runs-dir .claude/fluxpoint/runs]
             [--campaign NAME] [--json]
"""
import argparse
import json
import os
import sys

RUNS_DIR = os.path.join(".claude", "fluxpoint", "runs")
MEMORY = os.path.join(".claude", "fluxpoint", "memory.jsonl")
INBOX = os.path.join(".claude", "fluxpoint", "inbox.jsonl")


def read_runs(runs_dir):
    """[(runId, artifact)] sorted oldest first, plus unreadable findings."""
    runs, bad = [], []
    if not os.path.isdir(runs_dir):
        return runs, bad
    for fn in sorted(os.listdir(runs_dir)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(runs_dir, fn)
        try:
            with open(p, encoding="utf-8") as fh:
                art = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            bad.append(f"UNREADABLE {p}: {e}")
            continue
        if not isinstance(art, dict):
            bad.append(f"UNREADABLE {p}: not a run artifact (not an object)")
            continue
        runs.append((fn[: -len(".json")], art))
    runs.sort(key=lambda r: (str(r[1].get("when") or ""), r[0]))
    return runs, bad


def read_jsonl(path):
    """Rows plus named findings for lines that do not parse."""
    rows, bad = [], []
    if not os.path.exists(path):
        return rows, bad
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                bad.append(f"UNREADABLE {path}:{i}: {e}")
    return rows, bad


def _bump(d, k, by=1):
    d[k] = d.get(k, 0) + by


def fold(runs, lessons, inbox_rows):
    """Per-campaign aggregates. Pure fold over the artifacts, no I/O."""
    out = {}
    run_campaign = {}
    for run_id, art in runs:
        summary = art.get("summary") or {}
        name = str(summary.get("campaign") or "<unnamed>")
        run_campaign[run_id] = name
        c = out.setdefault(name, {
            "runs": 0, "outcomes": {},
            "nodesOk": 0, "nodesDead": 0, "nodesSkipped": 0, "nodesBlocked": 0,
            "spawned": 0, "planned": 0, "spent": 0, "runsWithoutSpendData": 0,
            "discovery": {"rounds": 0, "dryEnded": 0, "ceilingEnded": 0,
                          "found": 0, "fresh": 0, "kept": 0},
            "reduce": {"in": 0, "out": 0},
            "lessons": {"filed": 0, "killed": 0},
            "attestation": {},
            "inboxRaised": 0,
        })
        c["runs"] += 1
        _bump(c["outcomes"], str(art.get("outcome") or "UNKNOWN"))
        c["nodesOk"] += int(art.get("nodesOk") or 0)
        c["nodesDead"] += int(art.get("nodesDead") or 0)
        c["nodesSkipped"] += int(art.get("nodesSkipped") or 0)
        if isinstance(summary.get("spawned"), int):
            c["spawned"] += summary["spawned"]
            c["planned"] += int(summary.get("planned") or 0)
            c["spent"] += int(summary.get("spent") or 0)
        else:
            # A run compiled before these fields existed is a fact about the
            # history, not a zero to average in.
            c["runsWithoutSpendData"] += 1
        att = (art.get("attestation") or {}).get("tally") or {}
        for k, v in att.items():
            if isinstance(v, int):
                _bump(c["attestation"], k, v)
        saw_rounds = False
        ceilinged = False
        for p in summary.get("provenance") or []:
            st = p.get("status")
            if st == "BLOCKED":
                c["nodesBlocked"] += 1
            if st == "INCOMPLETE" and "ceiling" in str(p.get("detail") or ""):
                ceilinged = True
            d = p.get("data")
            if isinstance(d, dict):
                # Only the per-round tally notes count as rounds: a dead
                # round's note carries {round, returned, of} and no 'found',
                # and a partial-death round would otherwise be counted twice.
                if "round" in d and "found" in d:
                    saw_rounds = True
                    c["discovery"]["rounds"] += 1
                    c["discovery"]["found"] += int(d.get("found") or 0)
                    c["discovery"]["fresh"] += int(d.get("fresh") or 0)
                    c["discovery"]["kept"] += int(d.get("kept") or 0)
                if "before" in d and "after" in d:
                    c["reduce"]["in"] += int(d.get("before") or 0)
                    c["reduce"]["out"] += int(d.get("after") or 0)
        if saw_rounds:
            _bump(c["discovery"], "ceilingEnded" if ceilinged else "dryEnded")
    for row in lessons:
        prov = row.get("provenance") or {}
        name = run_campaign.get(str(prov.get("runId") or ""))
        if name is None:
            continue
        out[name]["lessons"]["filed"] += 1
        if row.get("status") == "killed":
            out[name]["lessons"]["killed"] += 1
    for row in inbox_rows:
        name = str(row.get("campaign") or "")
        if name in out:
            out[name]["inboxRaised"] += 1
    return out


def pct(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def render(folded):
    lines = []
    for name in sorted(folded):
        c = folded[name]
        outcomes = ", ".join(f"{k} {v}" for k, v in sorted(c["outcomes"].items()))
        lines.append(f"campaign: {name}")
        lines.append(f"  runs: {c['runs']} ({outcomes})")
        judged = c["nodesOk"] + c["nodesDead"]
        lines.append(
            f"  nodes: {c['nodesOk']} ok, {c['nodesDead']} dead "
            f"(death rate {pct(c['nodesDead'], judged)}), "
            f"{c['nodesSkipped']} skipped on budget, "
            f"{c['nodesBlocked']} blocked on a person")
        if c["runs"] > c["runsWithoutSpendData"]:
            lines.append(
                f"  agent calls: {c['spawned']} spawned of {c['planned']} "
                f"planned worst-case ({pct(c['spawned'], c['planned'])}); "
                f"{c['spent']} tokens by the runtime's meter")
        if c["runsWithoutSpendData"]:
            lines.append(
                f"  ({c['runsWithoutSpendData']} run(s) predate spawn/spend "
                f"recording and are not in those numbers)")
        d = c["discovery"]
        if d["rounds"]:
            lines.append(
                f"  discovery: {d['rounds']} round(s), {d['found']} found -> "
                f"{d['fresh']} new after dedup -> {d['kept']} kept; "
                f"{d['dryEnded']} sweep(s) ended on the dry rule, "
                f"{d['ceilingEnded']} on the ceiling")
        r = c["reduce"]
        if r["in"]:
            lines.append(
                f"  reduce: {r['in']} item(s) in -> {r['out']} out "
                f"(compression {pct(r['in'] - r['out'], r['in'])})")
        les = c["lessons"]
        if les["filed"]:
            lines.append(
                f"  verification: {les['filed']} lesson(s) filed, "
                f"{les['killed']} killed by panels "
                f"(kill rate {pct(les['killed'], les['filed'])})")
        if c["attestation"]:
            att = ", ".join(f"{k} {v}" for k, v in sorted(c["attestation"].items())
                            if k != "checked")
            lines.append(f"  attestation: {att or 'none recorded'}")
        if c["inboxRaised"]:
            lines.append(f"  inbox: raised {c['inboxRaised']} item(s) for a person")
        lines.append("")
    return "\n".join(lines).rstrip()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--runs-dir", default=None,
                    help=f"default: <root>/{RUNS_DIR}")
    ap.add_argument("--campaign", help="only this campaign")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    runs_dir = args.runs_dir or os.path.join(args.root, RUNS_DIR)
    runs, findings = read_runs(runs_dir)
    lessons, f2 = read_jsonl(os.path.join(args.root, MEMORY))
    inbox_rows, f3 = read_jsonl(os.path.join(args.root, INBOX))
    findings += f2 + f3

    for f in findings:
        print(f"metrics: {f}", file=sys.stderr)

    folded = fold(runs, lessons, inbox_rows)
    if args.campaign:
        folded = {k: v for k, v in folded.items() if k == args.campaign}

    if args.as_json:
        json.dump({"campaigns": folded, "findings": findings}, sys.stdout,
                  indent=2, sort_keys=True)
        print()
    elif not folded:
        print("metrics: no recorded runs" + (f" for campaign '{args.campaign}'"
                                             if args.campaign else ""))
    else:
        print(render(folded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
