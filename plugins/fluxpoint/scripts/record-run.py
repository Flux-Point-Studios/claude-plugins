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
# A frozen choice is a build artifact for the same reason Evidence is: the
# most expensive output of a campaign was the one thing with no slot, and a
# decision that lived only in a transcript was re-decided by the next
# context that had to ask the same question.
DEC_HDR = "| When (UTC) | Decision | Chosen | Overturned prior | Frozen by | Rationale |"


def cell(s, n=160):
    """One table cell: no pipes, no newlines, bounded."""
    s = str(s).replace("|", "\\|").replace("\n", " ").strip()
    return (s[: n - 1] + "…") if len(s) > n else s


def decision_rows(summary, ts):
    """A row per DecisionV1 the run produced, newest-run-first order.

    Imported decisions are excluded: they were decided — and filed — by the
    run named in decisionsImported, and re-filing them under every honoring
    campaign would stamp an old choice with a new date once per run.
    """
    results = summary.get("results") or {}
    contracts = summary.get("contracts") or {}
    decided = summary.get("decisions") or {}
    imported = summary.get("decisionsImported") or {}
    # Prefer the campaign's own decisions map; fall back to the contract map
    # so a graph that produced a DecisionV1 without `decides` is still filed.
    seen, rows = set(), []
    for did, rec in decided.items():
        if did in imported or not isinstance(rec, dict):
            continue
        seen.add(id(rec))
        rows.append((did, rec))
    for node, value in results.items():
        if contracts.get(node) == "DecisionV1" and isinstance(value, dict):
            if id(value) not in seen:
                rows.append((node, value))
    out = []
    for did, r in rows:
        out.append(
            f"| {ts} | {cell(did, 40)} | {cell(r.get('chosen'), 60)} "
            f"| {'YES' if r.get('overturned_prior') else 'no'} "
            f"| {cell(r.get('frozen_by') or 'none', 40)} "
            f"| {cell(r.get('rationale'))} |"
        )
    return out


def count_items(results, reducers=()):
    """Total array items across all node results.

    Deliberately generic: a review campaign's arrays are findings, a build
    campaign's are tests and evidence lines. The Evidence row therefore says
    "item(s)", not "finding(s)" — calling a slice's test list "verified
    findings" would overstate what the run actually established.

    Reduce nodes are excluded: their output is the source node's items,
    fewer, and counting both sides of a reduce would inflate "produced" by
    exactly the work the reducer exists to remove.
    """
    n = 0
    for node, v in (results or {}).items():
        if node in reducers:
            continue
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

    raw = open(args.result, encoding="utf-8").read() if args.result else sys.stdin.read()
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
    findings = count_items(summary.get("results"),
                           set(summary.get("reducers") or []))
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
        recs = summary.get("recommendations") or {}
        for p in prov:
            if p.get("status") == "BLOCKED":
                # The recommendation is the point of the row. An inbox item
                # that only says "blocked on you" moves the work to a person
                # without moving it forward.
                r = recs.get(p.get("node")) or {}
                detail = p.get("detail") or "waiting on a person"
                if r.get("chosen"):
                    detail = (f"RECOMMENDED: {r['chosen']}\n      WHY: "
                              f"{r.get('rationale', '')}\n      ASK: {detail}")
                    rejected = [o.get("option") for o in (r.get("options") or [])
                                if o.get("option") and o.get("option") != r["chosen"]]
                    if rejected:
                        detail += f"\n      ALSO CONSIDERED: {', '.join(rejected[:3])}"
                _inbox.add(args.root, "blocked", p.get("node"), campaign, detail)
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
                with open(os.path.join(wdir, f"{safe}.json"), "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(rec, fh, indent=2)
    except Exception as e:  # noqa: BLE001
        print(f"record-run: inbox/waits update failed: {e}", file=sys.stderr)

    # 1c. Cross-check every claimed gate exit against the hook-minted log.
    # Warn mode: a mismatch is said loudly and filed, but does not rewrite the
    # outcome yet. The corpus this produces is what earns the enforcing
    # version — a check that starts by failing runs is a check people turn
    # off before it has established what normal looks like.
    attestation = None
    try:
        import attest as _attest
        checks, afindings = _attest.verify_claims(args.root, summary)
        for f in afindings:
            print(f"record-run: {f}", file=sys.stderr)
        if checks:
            tally = {"checked": len(checks)}
            for st in ("ATTESTED", "UNATTESTED", "MISMATCH"):
                n = sum(1 for c in checks if c["status"] == st)
                if n:
                    tally[st.lower()] = n
            # Recorded separately because the two absences mean different
            # things: a claimed red with no row is expected (failures are not
            # attested), a claimed green with no row is not.
            sg = sum(1 for c in checks
                     if c["status"] == "UNATTESTED" and c.get("claimedExit") == 0)
            if sg:
                tally["unattested_claiming_pass"] = sg
            attestation = {"tally": tally, "checks": checks}
            for c in checks:
                stream = sys.stderr if c["status"] == "MISMATCH" else sys.stdout
                print(f"record-run: attest [{c['status']}] {c['node']} — {c['detail']}",
                      file=stream)
                if c["status"] == "MISMATCH":
                    import inbox as _ibx
                    _ibx.add(args.root, "attest-mismatch", c["node"], campaign,
                             c["detail"])
            # A node that declared `verify: prove:<gate>` asked to be held to
            # the hook's record. A contradiction there is not a warning: the
            # run's own verification says its exit codes are not what
            # happened, and a campaign does not get to file that as clean.
            # Nodes that merely happen to match a declared gate stay observed
            # rather than enforced — a check that starts by failing runs gets
            # switched off, and that lesson still holds for everyone who did
            # not opt in.
            tampered = [c for c in checks
                        if c["status"] == "MISMATCH" and c.get("declared")]
            unproven = [c for c in checks
                        if c["status"] == "UNATTESTED" and c.get("declared")]
            if tampered:
                outcome = "TAMPERED-EXECUTION"
                print(f"record-run: {len(tampered)} declared prove: node(s) "
                      f"contradict the attest log — filing this run as "
                      f"TAMPERED-EXECUTION", file=sys.stderr)
            elif unproven and outcome in ("COMPLETE", "UNKNOWN"):
                # The verification the graph declared did not happen. That is
                # not tampering — an executor that never routes through the
                # Bash tool leaves no rows — but it is not a clean run either.
                #
                # Absence is not equally suspicious in both directions, and
                # saying so is the only thing the cross-check can do about the
                # measured asymmetry: PostToolUse does not fire when a Bash
                # call fails, so a red gate is never attested. A node claiming
                # a NON-ZERO exit with no row is therefore consistent with a
                # gate that really failed — the log could not have recorded it.
                # A node claiming exit 0 with no row is the shape MISMATCH
                # exists to catch and structurally cannot: the runtime does
                # mint rows for passes, so a claimed pass should have left one.
                silent_green = [c for c in unproven if c.get("claimedExit") == 0]
                outcome = "INCOMPLETE"
                print(f"record-run: {len(unproven)} declared prove: node(s) cited "
                      f"no attestation — the declared verification did not run",
                      file=sys.stderr)
                if silent_green:
                    print(f"record-run: {len(silent_green)} of them claim exit 0 "
                          f"with no row. A pass leaves a row; a failure may not. "
                          f"Treat these as the unverified ones: "
                          + ", ".join(sorted(c["node"] for c in silent_green)),
                          file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a witness must never eat the record
        print(f"record-run: attestation cross-check failed: {e}", file=sys.stderr)

    # 1. Durable provenance artifact.
    os.makedirs(args.state_dir, exist_ok=True)
    art = os.path.join(args.state_dir, f"{args.run_id}.json")
    with open(art, "w", encoding="utf-8", newline="\n") as fh:
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
                "attestation": attestation,
                "summary": summary,
            },
            fh,
            indent=2,
        )

    # 1d. Lessons, filed from the run's own summary. Written after the run
    # artifact exists on purpose: memory.py refuses provenance that points at
    # no recorded run, which is the check that stops a fabricated summary
    # from planting durable knowledge.
    lessons = None
    try:
        import memory as _memory
        written, mfindings = _memory.append_from_summary(
            args.root, summary, args.run_id, args.state_dir)
        for f in mfindings:
            print(f"record-run: memory {f}", file=sys.stderr)
        if written:
            killed = sum(1 for r in written if r["status"] == "killed")
            lessons = {"filed": len(written), "killed": killed}
            print(f"record-run: filed {len(written)} lesson(s), {killed} killed")
    except Exception as e:  # noqa: BLE001 - never lose the Evidence row over this
        print(f"record-run: lesson filing failed: {e}", file=sys.stderr)

    # 1e. The recall index, refreshed after the lessons landed so the next
    # session's SessionStart section and the next campaign's seedmap rank
    # over what this run just filed. The index is a rebuildable projection
    # — a failure here costs recall freshness, never the Evidence row, and
    # the store stays the only truth. Dense backfill runs only when a key
    # is present; FPL_MEMORY_INDEX=0 / FPL_MEMORY_EMBED=0 opt out.
    recall_stats = None
    if os.environ.get("FPL_MEMORY_INDEX", "1") != "0":
        try:
            import recall as _recall
            recall_stats = _recall.refresh(
                args.root,
                embed=os.environ.get("FPL_MEMORY_EMBED", "1") != "0")
            print(f"record-run: recall index refreshed — "
                  f"{recall_stats['docs']} doc(s), dense "
                  f"{recall_stats['provider']}"
                  + (f", {recall_stats['pending']} pending"
                     if recall_stats.get("pending") else ""))
        except Exception as e:  # noqa: BLE001 - never lose the Evidence row over this
            print(f"record-run: recall index refresh failed: {e}",
                  file=sys.stderr)

    # 2. Evidence row, appended under whichever table header the file carries.
    claim = f"graph run: {ok} node(s) OK, {dead} dead, {findings} produced item(s)"
    if blocked:
        claim += "; red-team returned BLOCK — not shippable"
    if blocked_nodes:
        claim += (f"; {len(blocked_nodes)} node(s) BLOCKED on a person "
                  f"({', '.join(blocked_nodes[:3])})")
    if skipped:
        claim += f"; {skipped} SKIPPED on budget — coverage incomplete"
    seeded = summary.get("memorySeeded") or {}
    if seeded or lessons:
        total_seeded = sum(v for v in seeded.values() if isinstance(v, int))
        # A sweep standing on prior ground and one starting cold produce the
        # same finding count, so the row has to say which this was.
        parts = [f"seeded {total_seeded} prior key(s)"] if seeded else []
        if lessons:
            parts.append(f"filed {lessons['filed']} lesson(s), "
                         f"{lessons['killed']} killed")
        # The ranking universe is named so keyed and keyless teammates
        # producing different seed orders stay attributable from the row.
        if recall_stats:
            parts.append(f"recall: {recall_stats['provider']}")
        claim += "; memory: " + ", ".join(parts)
    imported = summary.get("decisionsImported") or {}
    if imported:
        claim += ("; honors " + ", ".join(
            f"{d}@{imported[d]}" for d in sorted(imported)[:2]))
        if len(imported) > 2:
            claim += f" +{len(imported) - 2} more imported decision(s)"
    for p in partial:
        claim += f"; {p.get('node')} INCOMPLETE — {p.get('detail') or 'did not run to exhaustion'}"
    if attestation:
        t = attestation["tally"]
        if t.get("mismatch"):
            claim += (f"; {t['mismatch']} gate claim(s) CONTRADICT the attested "
                      f"execution log — the exit codes are not trustworthy")
        elif t.get("unattested"):
            claim += (f"; {t['unattested']} gate claim(s) UNATTESTED — self-reported "
                      f"exit code(s), no hook-minted record")
    proof = f"harness exit {harness}; red-team {red_team}; executor {args.executor}"
    if attestation:
        att = attestation["tally"]
        proof += ("; attestation " + ", ".join(
            f"{k} {v}" for k, v in sorted(att.items()) if k != "checked"))
    row = f"| {ts} | {args.run_id} | {outcome} | {claim} | {proof} |"
    legacy_row = (
        f"| {ts} | {args.run_id} | {outcome} | {ok}/{dead} | {findings} "
        f"| {harness} | {red_team} |"
    )

    # 2a. Decisions, spliced under their own header when the file has one.
    drows = decision_rows(summary, ts)
    if drows and os.path.exists(args.graph):
        text = open(args.graph, encoding="utf-8").read()
        if DEC_HDR in text:
            text, n = re.subn(
                re.escape(DEC_HDR) + r"\n\|[-| ]+\|\n",
                lambda m: m.group(0) + "\n".join(drows) + "\n", text, count=1)
            if n:
                open(args.graph, "w", encoding="utf-8", newline="\n").write(text)
                for d in drows:
                    print(d)
        else:
            print(f"record-run: {len(drows)} decision(s) recorded in the run "
                  f"artifact, but {args.graph} has no Decisions table to append "
                  f"to — add one so a frozen choice outlives this run",
                  file=sys.stderr)

    if os.path.exists(args.graph):
        text = open(args.graph, encoding="utf-8").read()
        hdr, new_row = (ROW_HDR, row) if ROW_HDR in text else (LEGACY_HDR, legacy_row)
        if hdr in text:
            text, n = re.subn(
                re.escape(hdr) + r"\n\|[-| ]+\|\n",
                lambda m: m.group(0) + new_row + "\n",
                text,
                count=1,
            )
            if n:
                open(args.graph, "w", encoding="utf-8", newline="\n").write(text)
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
