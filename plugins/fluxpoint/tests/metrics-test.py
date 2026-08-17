#!/usr/bin/env python3
"""Metrics aggregator: the numbers must be recomputable, and gaps must be
named.

Pins three properties. The fold is correct against artifacts shaped like
what record-run.py and the compiled scripts actually write — including the
structured `data` rows the emitter attaches for discovery rounds and
reduces. Runs that predate the spawned/planned/spent fields are counted
and named, never averaged in as zeros. And a malformed artifact is
reported by name rather than silently skipped, because a metrics pass that
quietly dropped a run would read as a cleaner history than the repo has.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
METRICS = os.path.join(PLUGIN, "scripts", "metrics.py")

spec = importlib.util.spec_from_file_location("metrics", METRICS)
mx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mx)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print(f"{'PASS' if ok else 'FAIL'}  {name:<58} {'' if ok else '-> ' + str(detail)}")
    passed, failed = (passed + ok, failed + (not ok))


ROOT = tempfile.mkdtemp(prefix="fpl-metrics-")
RUNS = os.path.join(ROOT, ".claude", "fluxpoint", "runs")
os.makedirs(RUNS)

# A modern run: spawn/spend recorded, discovery rounds, a reduce, a block.
MODERN = {
    "runId": "wf-b", "when": "2026-08-16 10:00", "outcome": "INCOMPLETE",
    "nodesOk": 4, "nodesDead": 1, "nodesSkipped": 1,
    "attestation": {"tally": {"checked": 2, "attested": 1, "mismatch": 1}},
    "summary": {
        "campaign": "sweep", "spawned": 9, "planned": 20, "spent": 250000,
        "provenance": [
            {"node": "hunt", "status": "OK", "detail": "round 1",
             "data": {"round": 1, "found": 6, "fresh": 4, "kept": 3,
                      "perWorker": [3, 1]}},
            {"node": "hunt", "status": "DEAD", "detail": "round 2 death",
             "data": {"round": 2, "returned": 1, "of": 2}},
            {"node": "hunt", "status": "OK", "detail": "round 2",
             "data": {"round": 2, "found": 2, "fresh": 1, "kept": 1,
                      "perWorker": [1, 0]}},
            {"node": "hunt", "status": "INCOMPLETE",
             "detail": "ended on the 4-round ceiling with new items"},
            {"node": "cut", "status": "OK", "detail": "reduced",
             "data": {"before": 4, "after": 2}},
            {"node": "sign", "status": "BLOCKED", "detail": "waiting"},
        ],
    },
}
# A legacy run from before the spend fields existed.
LEGACY = {
    "runId": "wf-a", "when": "2026-08-15 09:00", "outcome": "COMPLETE",
    "nodesOk": 3, "nodesDead": 0, "nodesSkipped": 0,
    "summary": {"campaign": "sweep", "provenance": [
        {"node": "hunt", "status": "OK", "detail": "round 1",
         "data": {"round": 1, "found": 3, "fresh": 3, "kept": 2}},
    ]},
}
for art in (MODERN, LEGACY):
    with open(os.path.join(RUNS, art["runId"] + ".json"), "w",
              encoding="utf-8") as fh:
        json.dump(art, fh)

with open(os.path.join(ROOT, ".claude", "fluxpoint", "memory.jsonl"), "w",
          encoding="utf-8") as fh:
    rows = [
        {"tag": "t", "dedupeKey": "a|1", "claim": "a claim long enough to act on",
         "status": "surviving", "node": "hunt", "provenance": {"runId": "wf-b"}},
        {"tag": "t", "dedupeKey": "b|2", "claim": "another claim long enough",
         "status": "killed", "node": "hunt", "provenance": {"runId": "wf-b"}},
        {"tag": "t", "dedupeKey": "c|3", "claim": "an orphan from no known run",
         "status": "killed", "node": "hunt", "provenance": {"runId": "wf-zz"}},
    ]
    fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
with open(os.path.join(ROOT, ".claude", "fluxpoint", "inbox.jsonl"), "w",
          encoding="utf-8") as fh:
    fh.write(json.dumps({"id": "blocked:sweep:sign", "kind": "blocked",
                         "campaign": "sweep", "resolved": False}) + "\n")

runs, bad = mx.read_runs(RUNS)
lessons, _ = mx.read_jsonl(os.path.join(ROOT, ".claude", "fluxpoint", "memory.jsonl"))
inbox_rows, _ = mx.read_jsonl(os.path.join(ROOT, ".claude", "fluxpoint", "inbox.jsonl"))
folded = mx.fold(runs, lessons, inbox_rows)
c = folded.get("sweep") or {}

check("both runs folded, oldest first", c.get("runs") == 2, c)
check("outcome histogram", c.get("outcomes") == {"COMPLETE": 1, "INCOMPLETE": 1},
      c.get("outcomes"))
check("node totals summed", (c.get("nodesOk"), c.get("nodesDead"),
                             c.get("nodesSkipped")) == (7, 1, 1), c)
check("blocked counted from provenance", c.get("nodesBlocked") == 1, c)
check("spawn/spend summed from runs that carry them",
      (c.get("spawned"), c.get("planned"), c.get("spent")) == (9, 20, 250000), c)
check("legacy run named, not zero-averaged", c.get("runsWithoutSpendData") == 1, c)
d = c.get("discovery") or {}
check("rounds counted once each (death note not double-counted)",
      d.get("rounds") == 3, d)
check("found/fresh/kept summed", (d.get("found"), d.get("fresh"),
                                  d.get("kept")) == (11, 8, 6), d)
check("ceiling vs dry endings split", (d.get("ceilingEnded"),
                                       d.get("dryEnded")) == (1, 1), d)
check("reduce compression folded", c.get("reduce") == {"in": 4, "out": 2}, c)
check("kill rate from lessons, orphans excluded",
      c.get("lessons") == {"filed": 2, "killed": 1}, c)
check("attestation tally summed", c.get("attestation") == {"checked": 2,
      "attested": 1, "mismatch": 1}, c)
check("inbox items attributed", c.get("inboxRaised") == 1, c)

# A malformed artifact is named, never silently dropped.
with open(os.path.join(RUNS, "wf-broken.json"), "w", encoding="utf-8") as fh:
    fh.write("{not json")
r = subprocess.run([sys.executable, METRICS, "--root", ROOT],
                   capture_output=True, text=True)
check("CLI exits 0 with a report", r.returncode == 0, r.stderr[-200:])
check("malformed artifact named on stderr", "UNREADABLE" in r.stderr
      and "wf-broken" in r.stderr, r.stderr[-200:])
check("report still carries the good runs", "runs: 2" in r.stdout, r.stdout)
check("report names the legacy gap", "predate spawn/spend" in r.stdout, r.stdout)

r = subprocess.run([sys.executable, METRICS, "--root", ROOT, "--json"],
                   capture_output=True, text=True)
try:
    doc = json.loads(r.stdout)
    ok = doc["campaigns"]["sweep"]["reduce"] == {"in": 4, "out": 2}
except Exception as e:  # noqa: BLE001
    ok, doc = False, str(e)
check("--json output parses and matches the fold", ok, doc)

r = subprocess.run([sys.executable, METRICS, "--root",
                    tempfile.mkdtemp(prefix="fpl-empty-")],
                   capture_output=True, text=True)
check("empty repo says so rather than erroring", r.returncode == 0
      and "no recorded runs" in r.stdout, (r.returncode, r.stdout))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
