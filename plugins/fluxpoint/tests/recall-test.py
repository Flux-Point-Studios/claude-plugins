#!/usr/bin/env python3
"""Hybrid recall: the properties that make it safe to trust.

Executed, not inspected: every case builds a real store in a scratch git
repo, runs the real build and the real pipeline, and asserts on what came
out. The invariants worth pinning, in order of blast radius:

  * Determinism. The index is a projection; projecting twice from the same
    sources must be byte-identical, or resume/diff/review all rot.
  * Source discipline. A malformed line in memory.jsonl or a run artifact
    is a hard error, never a skipped row — an index built over a silently
    shrunk store believes it is standing on more ground than it is.
  * Advisory-only. Killed lessons are retrieved and labelled, never
    filtered; the seedmap shape matches memory.py --load exactly, so the
    compiled graph's empty-dedup-set contract survives untouched.
  * Named degradation. Keyless, stale-index, and index-absent modes each
    say what they are; none of them errors, and none is silent.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)
import recall  # noqa: E402
import embedder  # noqa: E402
import memory as memory_mod  # noqa: E402

passed = failed = 0


def report(name, ok, detail):
    global passed, failed
    ok = bool(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<56} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def scratch(rows, runs=None, files=None, cex=None):
    root = tempfile.mkdtemp(prefix="fpl-recall-")
    sh(root, "git", "init", "-q", ".")
    sh(root, "git", "config", "user.email", "t@t")
    sh(root, "git", "config", "user.name", "t")
    os.makedirs(os.path.join(root, ".claude", "fluxpoint", "runs"))
    for name, content in (files or {}).items():
        p = os.path.join(root, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
    for rid, art in (runs or {}).items():
        with open(os.path.join(root, ".claude", "fluxpoint", "runs",
                               f"{rid}.json"), "w") as fh:
            json.dump(art, fh)
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-qm", "init", "--allow-empty")
    with open(os.path.join(root, ".claude", "fluxpoint",
                           "memory.jsonl"), "w") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    if cex is not None:
        with open(os.path.join(root, ".fluxpoint-cex.jsonl"), "w") as fh:
            for r in cex:
                fh.write(json.dumps(r) + "\n")
    return root


def lesson(tag, key, claim, status="surviving", run="r-1",
           when="2026-08-01T10:00:00Z", objection="", node="find"):
    return {"tag": tag, "dedupeKey": key, "claim": claim, "status": status,
            "objection": objection, "kills": 2 if status == "killed" else 0,
            "node": node, "provenance": {"runId": run},
            "establishedWhen": when}


RUNS = {"r-1": {"runId": "r-1", "when": "2026-08-01T09:00:00Z",
                "summary": {"campaign": "beacon audit"}},
        "r-2": {"runId": "r-2", "when": "2026-08-10T09:00:00Z",
                "summary": {"campaign": "beacon audit"}}}
ROWS = [
    lesson("audit", "src/a.ts|two-way beacons unprefixed",
           "Two-way asset beacon names are unprefixed where one-way "
           "derivation prefixes them, so a shared decoder misreads"),
    lesson("audit", "src/missing.ts|fill budget",
           "Packing more than six fills exceeds the measured budget",
           status="killed", objection="measured against the v2 cost model"),
    lesson("hygiene", "gate/bash-writes",
           "Bash-written source never arms the dirty marker so the gate "
           "exits clean without a harness run", run="r-2",
           when="2026-08-10T10:00:00Z"),
]
FILES = {"src/a.ts": "export const a = 1\n"}


def quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        r = fn(*a, **kw)
    return r, out.getvalue(), err.getvalue()


# ==================== determinism ==========================================
root = scratch(ROWS, RUNS, FILES)
quiet(recall.build, root)
idx = os.path.join(root, recall.INDEX_DIR)
g1 = open(os.path.join(idx, recall.GRAPH_F), "rb").read()
l1 = open(os.path.join(idx, recall.LEX_F), "rb").read()
quiet(recall.build, root)
g2 = open(os.path.join(idx, recall.GRAPH_F), "rb").read()
l2 = open(os.path.join(idx, recall.LEX_F), "rb").read()
report("double build is byte-identical", g1 == g2 and l1 == l2,
       f"graph {len(g1)}B, lex {len(l1)}B")

graph = json.loads(g1)
report("unknown node kinds are impossible by construction",
       all(n["kind"] in recall.NODE_KINDS for n in graph["nodes"].values()),
       f"{len(graph['nodes'])} node(s), all registered kinds")

# ==================== source discipline ====================================
bad = scratch(ROWS + ["{not json"], RUNS, FILES)
try:
    quiet(recall.build, bad)
    report("malformed memory.jsonl line is fatal", False, "build succeeded")
except SystemExit as e:
    report("malformed memory.jsonl line is fatal", "not valid JSON" in str(e),
           str(e)[:70])
shutil.rmtree(bad)

badrun = scratch(ROWS, RUNS, FILES)
with open(os.path.join(badrun, ".claude", "fluxpoint", "runs",
                       "r-bad.json"), "w") as fh:
    fh.write("{broken")
try:
    quiet(recall.build, badrun)
    report("malformed run artifact is fatal", False, "build succeeded")
except SystemExit as e:
    report("malformed run artifact is fatal", "not readable JSON" in str(e),
           str(e)[:70])
shutil.rmtree(badrun)

# ==================== graph shape ==========================================
nid_killed = "lesson:audit|src/missing.ts|fill budget"
kinds = {tuple(e) for e in graph["edges"]}
report("a kill is a live KILLED_BY edge",
       (nid_killed, "KILLED_BY", "run:r-1") in kinds,
       "killed lesson -> its killing run")
report("TOUCHES resolves or drops, and the drop is counted",
       ("lesson:audit|src/a.ts|two-way beacons unprefixed", "TOUCHES",
        "file:src/a.ts") in kinds
       and not any(k[1] == "TOUCHES" and k[0] == nid_killed for k in kinds),
       "existing path edged, missing path dropped")
# Two candidates fail to resolve: src/missing.ts (a real path that is not
# in the tree) and gate/bash-writes (path-shaped prose). Both drops are
# counted — a heuristic edge must confess its misses.
stats, _, _ = quiet(recall.build, root)
report("--stats names every dropped path candidate",
       stats["droppedPaths"] == 2, f"droppedPaths={stats['droppedPaths']}")

# ==================== retrieval invariants =================================
(recs, diags), _, _ = quiet(recall.search, root,
                            query="beacon prefix decoder", k=5, offline=True)
ids = [r["id"] for r in recs]
report("query ranks the on-topic lesson first",
       ids and ids[0] == "lesson:audit|src/a.ts|two-way beacons unprefixed",
       ids[0] if ids else "no results")
report("killed lessons are retrieved and labelled, never suppressed",
       any(r["id"] == nid_killed and r["status"] == "killed" for r in recs),
       "killed row present in results")
report("keyless mode names the absent dense leg",
       any("dense leg absent" in d for d in diags), "; ".join(diags)[:70])

(recs2, _), _, _ = quiet(recall.search, root,
                         query="beacon prefix decoder", k=5, offline=True)
report("search is deterministic given the index",
       [r["id"] for r in recs] == [r["id"] for r in recs2],
       "identical ranking across runs")

# ==================== seedmap contract =====================================
(recs, _), _, _ = quiet(recall.search, root, query="beacon", tags=["audit"],
                        k=10, offline=True)
seed = recall.render_seedmap(root, recs, ["audit"])
base = memory_mod.seed_map(root, ["audit"])
report("seedmap keys match memory.py --load exactly (reordered only)",
       sorted(seed["audit"]["keys"]) == sorted(base["audit"]["keys"]),
       f"{len(seed['audit']['keys'])} key(s)")
report("seedmap killed rows carry claim + objection, no duplicates",
       seed["audit"]["killed"] == base["audit"]["killed"],
       json.dumps(seed["audit"]["killed"])[:70])
empty = scratch([], {}, {})
(r0, _), _, _ = quiet(recall.search, empty, query="anything", tags=["audit"],
                      offline=True)
report("empty store yields valid empty seedmap, not an error",
       recall.render_seedmap(empty, r0, ["audit"]) ==
       {"audit": {"keys": [], "killed": []}}, "cold start is a state")
shutil.rmtree(empty)

# ==================== temporal =============================================
super_rows = [
    lesson("audit", "k1", "The first reading of the invariant, later refined",
           run="r-1", when="2026-08-01T10:00:00Z"),
    lesson("audit", "k1", "The refined reading of the invariant that stands",
           run="r-2", when="2026-08-10T10:00:00Z"),
]
troot = scratch(super_rows, RUNS, {})
quiet(recall.build, troot)
(now_recs, _), _, _ = quiet(recall.search, troot, query="invariant reading",
                            k=5, offline=True)
(old_recs, _), _, _ = quiet(recall.search, troot, query="invariant reading",
                            k=5, offline=True, as_of="2026-08-05T00:00:00Z")
report("latest version serves by default",
       now_recs and "refined" in now_recs[0]["claim"],
       now_recs[0]["claim"][:60] if now_recs else "none")
report("--as-of serves the version valid at that time",
       old_recs and "first reading" in old_recs[0]["claim"]
       and old_recs[0]["runs"] == ["r-1"],
       old_recs[0]["claim"][:60] if old_recs else "none")
tg = json.loads(open(os.path.join(troot, recall.INDEX_DIR,
                                  recall.GRAPH_F)).read())
node = tg["nodes"]["lesson:audit|k1"]
report("re-establishment is kept as the identity's run chain",
       node["runs"] == ["r-1", "r-2"] and len(node["versions"]) == 2,
       f"runs={node['runs']}")

dead_rows = [lesson("audit", "k2", "A claim later marked superseded by "
                    "consolidation and replaced", status="superseded")]
droot = scratch(dead_rows, RUNS, {})
quiet(recall.build, droot)
(d1, _), _, _ = quiet(recall.search, droot, query="claim consolidation",
                      k=5, offline=True)
(d2, _), _, _ = quiet(recall.search, droot, query="claim consolidation",
                      k=5, offline=True, include_superseded=True)
report("superseded rows are filtered by default, reachable on request",
       not d1 and len(d2) == 1, f"default={len(d1)}, opted-in={len(d2)}")
shutil.rmtree(droot)

# ==================== stale kills ==========================================
sroot = scratch(
    [lesson("audit", "src/hot.ts|race in fill packing",
            "The packer double-spends an input when two fills share a UTxO",
            status="killed", objection="the guard upstream already rejects it",
            when="2020-01-01T00:00:00Z")],
    RUNS, {"src/hot.ts": "export const hot = 1\n"})
sstats, _, _ = quiet(recall.build, sroot)
sg = json.loads(open(os.path.join(sroot, recall.INDEX_DIR,
                                  recall.GRAPH_F)).read())
snode = sg["nodes"]["lesson:audit|src/hot.ts|race in fill packing"]
(srecs, _), _, _ = quiet(recall.search, sroot,
                         query="packer double-spends a UTxO input",
                         k=3, offline=True)
report("a kill whose file changed since is marked stale, not dropped",
       snode.get("stale") == "src/hot.ts" and sstats["staleKills"] == 1
       and srecs and srecs[0].get("stale") == "src/hot.ts",
       "annotated and still retrieved")
shutil.rmtree(sroot)

# ==================== episode-mentions boost ===============================
one = scratch([lesson("audit", "k3", "The planner emits a wrong redeemer "
                      "index for two-way fills")], RUNS, {})
two = scratch([lesson("audit", "k3", "The planner emits a wrong redeemer "
                      "index for two-way fills"),
               lesson("audit", "k3", "The planner emits a wrong redeemer "
                      "index for two-way fills", run="r-2",
                      when="2026-08-10T10:00:00Z")], RUNS, {})
quiet(recall.build, one)
quiet(recall.build, two)
(o1, _), _, _ = quiet(recall.search, one, query="planner redeemer index",
                      k=1, offline=True)
(o2, _), _, _ = quiet(recall.search, two, query="planner redeemer index",
                      k=1, offline=True)
report("re-established lessons outrank one-off equals",
       o1 and o2 and o2[0]["score"] > o1[0]["score"],
       f"{o2[0]['score']:.5f} > {o1[0]['score']:.5f}"
       if o1 and o2 else "missing results")
shutil.rmtree(one)
shutil.rmtree(two)

# ==================== freshness policy =====================================
with open(os.path.join(root, ".claude", "fluxpoint", "memory.jsonl"),
          "a") as fh:
    fh.write(json.dumps(lesson("audit", "k-new",
                               "A lesson filed after the last index build "
                               "arrives here")) + "\n")
report("--verify goes red when sources moved", recall.is_stale(root),
       "fingerprint mismatch detected")
(recs, diags), _, _ = quiet(recall.search, root, query="beacon", k=3,
                            offline=True, allow_rebuild=False)
report("hooks use the stale index and say so",
       recs and any("lags the stores" in d for d in diags),
       "; ".join(diags)[:70])
(recs, diags), _, _ = quiet(recall.search, root, query="beacon", k=3,
                            offline=True)
report("CLI rebuilds on staleness", not recall.is_stale(root)
       and not any("lags" in d for d in diags), "rebuilt in place")

# ==================== offline means offline ================================
def _explode(*a, **kw):
    raise AssertionError("network attempted in offline mode")


orig = embedder._request
embedder._request = _explode
try:
    env_had = os.environ.get("VOYAGE_API_KEY")
    os.environ["VOYAGE_API_KEY"] = "k-test-not-real"
    (recs, diags), _, _ = quiet(recall.search, root, query="beacon", k=3,
                                offline=True)
    report("--offline attempts no network even with a key present",
           any("offline mode" in d for d in diags), "; ".join(diags)[:70])
finally:
    embedder._request = orig
    if env_had is None:
        del os.environ["VOYAGE_API_KEY"]
    else:
        os.environ["VOYAGE_API_KEY"] = env_had

# ==================== for-session modes ====================================
def run_cli(cwd, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "recall.py"), *args],
        cwd=cwd, capture_output=True, text=True, timeout=60)

with open(os.path.join(root, "WORK.md"), "w") as fh:
    fh.write("# beacon decoder hardening\nSTATUS: IN PROGRESS\n\n## Plan\n"
             "- [ ] normalize two-way beacon names at every boundary\n")
r = run_cli(root, "--for-session")
report("for-session emits capped factual lines and exits 0",
       r.returncode == 0 and "advisory" in r.stdout
       and len(r.stdout.splitlines()) <= 8,
       f"{len(r.stdout.splitlines())} line(s)")
shutil.rmtree(os.path.join(root, recall.INDEX_DIR))
r = run_cli(root, "--for-session")
report("for-session with no index falls back, named",
       r.returncode == 0 and "not built yet" in r.stdout
       and "Newest filed lessons" in r.stdout, r.stdout.splitlines()[0][:70])
noroot = scratch([], {}, {})
os.remove(os.path.join(noroot, ".claude", "fluxpoint", "memory.jsonl"))
r = run_cli(noroot, "--for-session")
report("for-session with no memory at all stays silent",
       r.returncode == 0 and r.stdout.strip() == "", "no store, no lines")
shutil.rmtree(noroot)

# ==================== render budget ========================================
quiet(recall.build, root)
(recs, diags), _, _ = quiet(recall.search, root, query="beacon gate fill",
                            k=10, offline=True)
text = recall.render_lines(recs, diags, budget_bytes=120)
report("budget overflow is elided by name",
       "[elided:" in text and len(text) <= 220, f"{len(text)} byte(s)")

shutil.rmtree(root)
shutil.rmtree(troot)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
