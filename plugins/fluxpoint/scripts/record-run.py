#!/usr/bin/env python3
"""Record a graph run: provenance artifact + WORK.md Evidence row.

Evidence stops being something an agent remembers to write by hand and
becomes a build artifact of the run. Reads the workflow's returned summary
JSON on stdin (or --result FILE).

Usage:
  record-run.py --run-id wf_abc --graph WORK.md [--harness 0]
                [--red-team SHIP] [--executor workflow] < result.json
"""
import argparse
import datetime
import json
import os
import re
import sys

# One Evidence discipline for both modes: loop slices write their own rows,
# graph runs get theirs appended here.
ROW_HDR = "| When (UTC) | Source | Outcome | Claim | Proof |"
LEGACY_HDR = "| When (UTC) | runId | Outcome | Nodes OK/dead | Findings | Harness | Red-team |"


def count_items(results):
    """Total verified items the graph produced across all node results."""
    n = 0
    for v in (results or {}).values():
        if isinstance(v, list):
            n += len(v)
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, list):
                    n += len(vv)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--graph", default="WORK.md", help="work-state file carrying the Evidence table")
    ap.add_argument("--result", help="file holding the workflow's return value (default stdin)")
    ap.add_argument("--harness", default="n/a", help="independent harness exit code")
    ap.add_argument("--red-team", default="n/a", help="SHIP | BLOCK | n/a")
    ap.add_argument("--executor", default="workflow", help="workflow | degraded-subagents")
    ap.add_argument("--state-dir", default=".claude/fluxpoint/runs")
    args = ap.parse_args()

    raw = open(args.result).read() if args.result else sys.stdin.read()
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"record-run: result is not JSON: {e}", file=sys.stderr)
        return 1

    prov = summary.get("provenance") or []
    ok = sum(1 for p in prov if p.get("status") == "OK")
    dead = sum(1 for p in prov if p.get("status") == "DEAD")
    # Work declined for budget is neither success nor failure, and must never
    # be filed as either — a skipped node means the campaign covered less
    # ground than it set out to.
    skipped = sum(1 for p in prov if p.get("status") == "SKIPPED")
    outcome = summary.get("outcome", "UNKNOWN")
    findings = count_items(summary.get("results"))
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")

    # 1. Durable provenance artifact.
    os.makedirs(args.state_dir, exist_ok=True)
    art = os.path.join(args.state_dir, f"{args.run_id}.json")
    with open(art, "w") as fh:
        json.dump(
            {
                "runId": args.run_id,
                "when": ts,
                "executor": args.executor,
                "outcome": outcome,
                "nodesOk": ok,
                "nodesDead": dead,
                "nodesSkipped": skipped,
                "findings": findings,
                "harnessExit": args.harness,
                "redTeam": args.red_team,
                "summary": summary,
            },
            fh,
            indent=2,
        )

    # 2. Evidence row, appended under whichever table header the file carries.
    claim = f"graph run: {ok} node(s) OK, {dead} dead, {findings} verified finding(s)"
    if skipped:
        claim += f"; {skipped} SKIPPED on budget — coverage incomplete"
    proof = f"harness exit {args.harness}; red-team {args.red_team}; executor {args.executor}"
    row = f"| {ts} | {args.run_id} | {outcome} | {claim} | {proof} |"
    legacy_row = (
        f"| {ts} | {args.run_id} | {outcome} | {ok}/{dead} | {findings} "
        f"| {args.harness} | {args.red_team} |"
    )

    if os.path.exists(args.graph):
        text = open(args.graph).read()
        hdr, new_row = (ROW_HDR, row) if ROW_HDR in text else (LEGACY_HDR, legacy_row)
        if hdr in text:
            text, n = re.subn(
                re.escape(hdr) + r"\n\|[-| ]+\|\n",
                lambda m: m.group(0) + new_row + "\n",
                text,
                count=1,
            )
            if n:
                open(args.graph, "w").write(text)
                row = new_row
            else:
                print(
                    f"record-run: Evidence header found in {args.graph} but no "
                    f"separator row beneath it; artifact written, row not appended",
                    file=sys.stderr,
                )
        else:
            print(
                f"record-run: no Evidence table header in {args.graph}; "
                f"artifact written but row not appended",
                file=sys.stderr,
            )
    print(f"record-run: {art}")
    print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
