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
    """Total array items across all node results.

    Deliberately generic: a review campaign's arrays are findings, a build
    campaign's are tests and evidence lines. The Evidence row therefore says
    "item(s)", not "finding(s)" — calling a slice's test list "verified
    findings" would overstate what the run actually established.
    """
    n = 0
    for v in (results or {}).values():
        if isinstance(v, list):
            n += len(v)
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, list):
                    n += len(vv)
    return n


def _each(value):
    """Yield the result objects a node produced: one, or one per item."""
    if isinstance(value, list):
        for v in value:
            if isinstance(v, dict):
                yield v
    elif isinstance(value, dict):
        yield value


def derive(summary):
    """Read the harness exit and red-team verdict out of the run itself.

    These arrive as CLI flags defaulting to 'n/a', which makes the two
    columns that decide whether a campaign shipped depend on a live
    orchestrator remembering to pass them. The run already knows: nodes
    carry declared contracts, and the compiler emits the nodeId -> contract
    map precisely so this does not have to guess from a result's shape.

    Returns (harness_exit, red_team_verdict, blocked) with None where the
    campaign genuinely produced no such node.
    """
    results = summary.get("results") or {}
    contracts = summary.get("contracts") or {}
    harness, verdict, blocked = None, None, False
    for node, value in results.items():
        c = contracts.get(node)
        for r in _each(value):
            if c == "HarnessCheckV1" or (c is None and "exit" in r):
                e = r.get("exit")
                if e is not None:
                    # Worst exit across every harness node: one red run is
                    # the campaign's answer, whatever a later one says.
                    harness = e if harness in (None, 0) else harness
            if c == "RedTeamV1" or (c is None and r.get("verdict") in ("SHIP", "BLOCK")):
                v = r.get("verdict")
                if v == "BLOCK":
                    verdict, blocked = "BLOCK", True
                elif v and verdict is None:
                    verdict = v
    return harness, verdict, blocked


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--graph", default="WORK.md", help="work-state file carrying the Evidence table")
    ap.add_argument("--result", help="file holding the workflow's return value (default stdin)")
    ap.add_argument("--harness", default="n/a", help="independent harness exit code")
    ap.add_argument("--red-team", default="n/a", help="SHIP | BLOCK | n/a")
    ap.add_argument("--executor", default="workflow", help="workflow | degraded-subagents")
    ap.add_argument("--state-dir", default=".claude/fluxpoint/runs")
    ap.add_argument("--root", default=".", help="repo root holding .claude/fluxpoint")
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
    partial = [p for p in prov if p.get("status") == "INCOMPLETE"]
    outcome = summary.get("outcome", "UNKNOWN")
    findings = count_items(summary.get("results"))
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")

    # Derived beats declared: the flags are a fallback for a run whose
    # summary carries no such node, never an override of one that does.
    d_harness, d_verdict, blocked = derive(summary)
    harness = str(d_harness) if d_harness is not None else args.harness
    red_team = d_verdict if d_verdict is not None else args.red_team
    if blocked and outcome in ("COMPLETE", "INCOMPLETE", "UNKNOWN"):
        # A campaign does not get to report COMPLETE over a blocking
        # verdict it collected. Halting is the graph's job; refusing to
        # file the run as clean is this script's.
        outcome = "BLOCKED-REDTEAM"

    # 1a. Irreversible effects go into the once-only ledger first. Written
    # before anything else in this script, because a row missing here is the
    # one that lets a resume perform a chain write twice.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import ledger as _ledger
        for r in _ledger.append_from_summary(args.root, summary, args.run_id):
            print(f"record-run: ledger recorded {r['node']} — {r['evidence']}")
    except Exception as e:  # noqa: BLE001 - never lose the Evidence row over this
        print(f"record-run: ledger append failed: {e}", file=sys.stderr)

    # 1b. Park the waits and raise anything that needs a person. A campaign
    # that parks instead of halting is only an improvement if somebody finds
    # out; otherwise it is a quieter failure than the halt it replaced.
    campaign = summary.get("campaign", "")
    blocked_nodes = [p.get("node") for p in prov if p.get("status") == "BLOCKED"]
    try:
        import inbox as _inbox
        for p in prov:
            if p.get("status") == "BLOCKED":
                _inbox.add(args.root, "blocked", p.get("node"), campaign,
                           p.get("detail") or "waiting on a person")
        if outcome == "CONFIRM-REQUIRED":
            refused = [p.get("node") for p in prov if p.get("status") == "REFUSED"]
            _inbox.add(args.root, "confirm-required", refused[0] if refused else "",
                       campaign,
                       "an irreversible node refused to fire without being named "
                       "in confirm")
        waits = summary.get("waits") or []
        if waits:
            wdir = os.path.join(args.root, ".claude", "fluxpoint", "waits")
            os.makedirs(wdir, exist_ok=True)
            for w in waits:
                rec = dict(w)
                rec["campaign"] = campaign
                # The resume point rides with the wait: a poller that knows
                # the condition cleared but not which run to continue has
                # moved the problem rather than solved it.
                rec["runId"] = args.run_id
                rec.setdefault("lastChecked", 0)
                safe = "".join(ch if ch.isalnum() or ch in "-_" else "-"
                               for ch in f"{campaign}.{w.get('node','')}")[:120]
                with open(os.path.join(wdir, f"{safe}.json"), "w") as fh:
                    json.dump(rec, fh, indent=2)
    except Exception as e:  # noqa: BLE001
        print(f"record-run: inbox/waits update failed: {e}", file=sys.stderr)

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
                "harnessExit": harness,
                "redTeam": red_team,
                "summary": summary,
            },
            fh,
            indent=2,
        )

    # 2. Evidence row, appended under whichever table header the file carries.
    claim = f"graph run: {ok} node(s) OK, {dead} dead, {findings} produced item(s)"
    if blocked:
        claim += "; red-team returned BLOCK — not shippable"
    if blocked_nodes:
        claim += (f"; {len(blocked_nodes)} node(s) BLOCKED on a person "
                  f"({', '.join(blocked_nodes[:3])})")
    if skipped:
        claim += f"; {skipped} SKIPPED on budget — coverage incomplete"
    for p in partial:
        claim += f"; {p.get('node')} INCOMPLETE — {p.get('detail') or 'did not run to exhaustion'}"
    proof = f"harness exit {harness}; red-team {red_team}; executor {args.executor}"
    row = f"| {ts} | {args.run_id} | {outcome} | {claim} | {proof} |"
    legacy_row = (
        f"| {ts} | {args.run_id} | {outcome} | {ok}/{dead} | {findings} "
        f"| {harness} | {red_team} |"
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
