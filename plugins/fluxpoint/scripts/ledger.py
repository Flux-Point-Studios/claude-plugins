#!/usr/bin/env python3
"""Once-only ledger for irreversible campaign nodes.

`mutates: true` buys `isolation: 'worktree'`, which is real containment for
a filesystem write and none whatsoever for a chain write. The same marker
covering "edit a test file" and "mint a one-shot NFT" reads to a later
author as protection it does not provide.

The sharper problem is that the recovery path this plugin actively
prescribes — repair one node, resume, replay the unchanged prefix — is
precisely the operation that performs an unrepeatable effect twice. Any
targeted repair upstream of a genesis node re-fires the genesis.

So an irreversible node's effect is recorded here, keyed by campaign, node
id, and a hash of the resolved prompt. `--load` hands that map to the
compiled graph as `args._ledger`; the generated code consults it before
every irreversible spawn and short-circuits on a hit. Replay-safety is
therefore structural, not something the operator has to remember at 2am.

What this does NOT cover, stated plainly: a crash between the effect
landing and the run ending. The workflow sandbox has no filesystem, so the
record cannot be written at the moment of the effect — it is written from
the run summary afterwards. The window is real. The confirm gate is what
stands in it: a human names the node before anything fires.

  ledger.py --load --campaign NAME     print {key: record} for args._ledger
  ledger.py --list                     human-readable, what has already fired
  ledger.py --append --run-id ID       append records from a run summary
"""
import argparse
import datetime
import json
import os
import sys

LEDGER_PATH = os.path.join(".claude", "fluxpoint", "irreversible.jsonl")


def path_for(root):
    return os.path.join(root, LEDGER_PATH)


def read(root):
    """Every recorded effect, oldest first. A malformed line is a hard error.

    Skipping an unparseable row would silently shrink the set of effects
    known to have happened, which is the one direction this file must never
    fail in.
    """
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
                raise SystemExit(f"ledger: {p}:{i} is not valid JSON: {e}")
    return out


def evidence_of(result):
    """A short human-readable proof string, best effort.

    Never invented: if the node returned nothing that looks like evidence,
    this says so rather than producing a confident-looking blank.
    """
    if not isinstance(result, dict):
        return "no structured result"
    for k in ("txHash", "tx_hash", "hash", "txid"):
        if isinstance(result.get(k), str) and result[k]:
            return f"{k}={result[k]}"
    ev = result.get("evidence")
    if isinstance(ev, list) and ev:
        first = ev[0]
        return str(first if not isinstance(first, dict) else json.dumps(first))[:200]
    if isinstance(ev, str) and ev:
        return ev[:200]
    return "recorded, no evidence field in the contract result"


def append_from_summary(root, summary, run_id):
    """Append a row per irreversible effect the run performed.

    Returns the rows written. A run that replayed from the ledger writes
    nothing new, because the compiled graph only pushes to `ledger` on the
    branch that actually spawned.
    """
    rows = summary.get("ledger") or []
    if not rows:
        return []
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    known = {r.get("key") for r in read(root)}
    written = []
    p = path_for(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            key = r.get("key")
            if not key or key in known:
                continue
            row = {
                "key": key,
                "campaign": r.get("campaign"),
                "node": r.get("node"),
                "runId": run_id,
                "when": when,
                "evidence": evidence_of(r.get("result")),
                "result": r.get("result"),
            }
            fh.write(json.dumps(row) + "\n")
            written.append(row)
            known.add(key)
    return written


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--campaign", help="filter to one campaign (required for --load)")
    ap.add_argument("--run-id", default="unknown")
    ap.add_argument("--result", help="run summary JSON file (default stdin) for --append")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--load", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--append", action="store_true")
    a = ap.parse_args()

    if a.load:
        if not a.campaign:
            ap.error("--load requires --campaign")
        # Always valid JSON, even when empty: the compiled graph refuses to
        # start on a missing ledger, and "{}" is a first run, not a missing one.
        out = {r["key"]: r for r in read(a.root)
               if r.get("campaign") == a.campaign and r.get("key")}
        print(json.dumps(out))
        return 0

    if a.list:
        rows = read(a.root)
        if not rows:
            print("ledger: no irreversible effects recorded")
            return 0
        print(f"ledger: {len(rows)} irreversible effect(s) already performed")
        for r in rows:
            print(f"  {r.get('when')}  {r.get('campaign')} / {r.get('node')}")
            print(f"      run {r.get('runId')}  {r.get('evidence')}")
        return 0

    raw = open(a.result, encoding="utf-8").read() if a.result else sys.stdin.read()
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ledger: result is not JSON: {e}", file=sys.stderr)
        return 1
    written = append_from_summary(a.root, summary, a.run_id)
    if written:
        for r in written:
            print(f"ledger: recorded {r['campaign']} / {r['node']} — {r['evidence']}")
    else:
        print("ledger: no new irreversible effects in this run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
