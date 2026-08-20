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
                "summary": {"campaign": "beacon audit",
                            "decisions": {"beacon-normalization": {
                                "question": "where do two-way beacon names "
                                            "get normalized?",
                                "chosen": "inside every consumer separately",
                                "rationale": "each consumer knows its own "
                                             "tolerance for raw validator "
                                             "bytes",
                            }}}},
        "r-2": {"runId": "r-2", "when": "2026-08-10T09:00:00Z",
                "summary": {"campaign": "beacon audit",
                            "decisions": {"beacon-normalization": {
                                "question": "where do two-way beacon names "
                                            "get normalized?",
                                "chosen": "at the decode boundary",
                                "rationale": "every consumer downstream then "
                                             "sees one canonical form and the "
                                             "validator's raw bytes stay "
                                             "quarantined in one module",
                            }}}}}
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

# Decisions project from run artifacts by their import id — the one row
# class with a stable machine identity — never from the markdown table.
dnode = graph["nodes"].get("decision:beacon-normalization")
report("a decision is a retrievable node keyed by its import id",
       dnode and dnode["kind"] == "decision"
       and "decode boundary" in dnode["text"]
       and dnode["runs"] == ["r-1", "r-2"]
       and len(dnode["versions"]) == 2,
       dnode["text"][:60] if dnode else "missing")
report("the decision is edged to every run that decided it",
       ("decision:beacon-normalization", "DECIDED_IN", "run:r-2") in kinds
       and ("decision:beacon-normalization", "DECIDED_IN", "run:r-1")
       in kinds,
       "DECIDED_IN present for both runs")
(drecs, _), _, _ = quiet(recall.search, root,
                         query="where are beacon names normalized canonical",
                         k=3, offline=True)
report("decisions surface from a topical query",
       any(r["id"] == "decision:beacon-normalization" for r in drecs),
       [r["id"] for r in drecs][0] if drecs else "no results")
(dold, _), _, _ = quiet(recall.search, root,
                        query="where are beacon names normalized canonical",
                        k=3, offline=True, as_of="2026-08-05T00:00:00Z")
old_view = next((r for r in dold
                 if r["id"] == "decision:beacon-normalization"), None)
report("--as-of serves the decision that governed then, not today's",
       old_view and old_view["chosen"] == "inside every consumer separately"
       and old_view["runs"] == ["r-1"],
       old_view["chosen"] if old_view else "missing")
(dnone, _), _, _ = quiet(recall.search, root,
                         query="where are beacon names normalized canonical",
                         k=3, offline=True, as_of="2026-07-01T00:00:00Z")
report("--as-of before any deciding run omits the decision",
       not any(r["id"] == "decision:beacon-normalization" for r in dnone),
       "an undecided id has no governing version")

# ==================== substrate primitives =================================
proot = scratch(
    [lesson("audit", "src/codec.ts|min-utxo sizing drifts",
            "The min-UTxO sizing in the codec drifts from the node's "
            "calculation on multi-asset outputs")],
    RUNS,
    {"src/codec.ts": "export const codec = 1\n",
     "substrate.json": json.dumps({
         "repo": "filler",
         "primitives": [
             {"id": "plutus-cbor-codec", "kind": "toolkit", "status": "live",
              "desc": "Hand-rolled CBOR reader/writer with min-UTxO sizing" + chr(1)
                      + chr(0x2028) + "forged line",
              "paths": ["src/codec.ts"], "consumes": []},
             {"id": "swap-planners", "kind": "engine", "status": "live",
              "desc": "Pure recipe builders for create, cancel and fill",
              "paths": ["src/missing"], "consumes": ["plutus-cbor-codec",
                                                     "foreign-primitive"]},
         ]})})
pstats, _, _ = quiet(recall.build, proot)
pg = json.loads(open(os.path.join(proot, recall.INDEX_DIR,
                                  recall.GRAPH_F)).read())
pkinds = {tuple(e) for e in pg["edges"]}
report("primitives project from the repo's own manifest",
       pstats["primitives"] == 2
       and pg["nodes"]["prim:plutus-cbor-codec"]["kind"] == "primitive",
       f"{pstats['primitives']} primitive(s)")
report("manifest strings are sanitized before they can reach a prompt",
       chr(1) not in pg["nodes"]["prim:plutus-cbor-codec"]["text"]
       and chr(0x2028) not in pg["nodes"]["prim:plutus-cbor-codec"]["text"],
       "control chars stripped")
report("consumes edges stay walkable across the repo boundary",
       ("prim:swap-planners", "CONSUMES", "prim:plutus-cbor-codec") in pkinds
       and ("prim:swap-planners", "CONSUMES", "prim:foreign-primitive")
       in pkinds and pg["nodes"]["prim:foreign-primitive"]["text"]
       == "foreign-primitive",
       "declared + stub targets edged")
report("a primitive shares file nodes with the lessons that touch it",
       ("prim:plutus-cbor-codec", "LOCATES", "file:src/codec.ts") in pkinds
       and ("lesson:audit|src/codec.ts|min-utxo sizing drifts", "TOUCHES",
            "file:src/codec.ts") in pkinds,
       "LOCATES and TOUCHES meet at file:src/codec.ts")
(precs, _), _, _ = quiet(recall.search, proot,
                         query="CBOR reader writer min-UTxO sizing",
                         k=4, offline=True)
report("primitives surface from a topical query",
       any(r["id"] == "prim:plutus-cbor-codec" for r in precs),
       [r["id"] for r in precs][:2] if precs else "no results")
shutil.rmtree(proot)

# A hostile or typo'd manifest must degrade, never crash or escape: wrong
# container types are dropped, and a path pointing outside the repo (or an
# absolute one) never becomes a file node however real the file it names.
hroot = scratch([], RUNS, {
    "substrate.json": json.dumps({
        "repo": "filler",
        "primitives": [
            {"id": "typo", "kind": "engine", "status": "live",
             "desc": "paths and consumes are the wrong container type here",
             "paths": "src/codec.ts", "consumes": {"a": 1}},
            {"id": "escape", "kind": "engine", "status": "live",
             "desc": "paths that try to leave the repository tree",
             "paths": ["/etc/passwd", "../..", "../escape.ts"],
             "consumes": []},
        ]})})
hstats, _, _ = quiet(recall.build, hroot)
hg = json.loads(open(os.path.join(hroot, recall.INDEX_DIR,
                                  recall.GRAPH_F)).read())
report("wrong container types in a manifest degrade instead of crashing",
       hstats["primitives"] == 2
       and not any(e[1] in ("LOCATES", "CONSUMES") for e in hg["edges"]),
       "typo'd primitive indexed with no junk edges")
report("a manifest path cannot edge the graph outside the repo",
       not any(n.startswith("file:") for n in hg["nodes"]),
       "no file nodes from absolute or ..-paths")
shutil.rmtree(hroot)

# Claims are agent-authored text landing in model-visible context: a
# control character in one must not survive into a rendered line.
croot = scratch([lesson("audit", "k-ctl",
                        "A claim carrying a control" + chr(1) + chr(0x2028)
                        + "character that must not forge lines")], RUNS, {})
quiet(recall.build, croot)
(crecs, _), _, _ = quiet(recall.search, croot,
                         query="claim carrying control character forge",
                         k=2, offline=True)
ctext = recall.render_lines(crecs, [])
report("rendered lines strip control characters from claims",
       crecs and chr(1) not in ctext and chr(0x2028) not in ctext
       and "forge lines" in ctext,
       "control chars stripped, text intact")
shutil.rmtree(croot)

# ==================== retrieval invariants =================================
(recs, diags), _, _ = quiet(recall.search, root,
                            query="beacon prefix decoder", k=5, offline=True)
ids = [r["id"] for r in recs]
lesson_ids = [r["id"] for r in recs if r["kind"] == "lesson"]
report("query ranks the on-topic lesson first among lessons",
       lesson_ids and lesson_ids[0] ==
       "lesson:audit|src/a.ts|two-way beacons unprefixed",
       lesson_ids[0] if lesson_ids else "no lesson results")
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
gone = not any(r["id"] == "lesson:audit|k2" for r in d1)
back = any(r["id"] == "lesson:audit|k2" for r in d2)
report("superseded rows are filtered by default, reachable on request",
       gone and back, f"default hides it={gone}, opted-in shows it={back}")
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
skill = next((r for r in srecs
              if r["id"] == "lesson:audit|src/hot.ts|race in fill packing"),
             None)
report("a kill whose file changed since is marked stale, not dropped",
       snode.get("stale") == "src/hot.ts" and sstats["staleKills"] == 1
       and skill is not None and skill.get("stale") == "src/hot.ts",
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

# ==================== per-prompt hook (dark launch) ========================
quiet(recall.build, root)  # the for-session cases above removed the index
PROMPT_SH = os.path.join(SCRIPTS, "prompt-recall.sh")
hook_json = json.dumps({"prompt": "why do two-way beacon names misread in "
                                  "the decoder?", "cwd": root})


def run_hook(env_extra, stdin):
    env = {k: v for k, v in os.environ.items() if k != "FPL_MEM_PROMPT"}
    env.update(env_extra)
    env["CLAUDE_PROJECT_DIR"] = root
    return subprocess.run(["bash", PROMPT_SH], input=stdin, env=env,
                          capture_output=True, text=True, timeout=60)

r = run_hook({}, hook_json)
report("the per-prompt hook is dark by default",
       r.returncode == 0 and r.stdout == "", "no gate flag, no output")
r = run_hook({"FPL_MEM_PROMPT": "1"}, hook_json)
report("armed, it injects corroborated items under budget",
       r.returncode == 0 and "advisory" in r.stdout
       and len(r.stdout) <= 1400
       and "beacons unprefixed" in r.stdout,
       f"{len(r.stdout)} byte(s)")
# The floor must be a floor: a prompt sharing only stopword-grade tokens
# with the store injects nothing. Lex+graph is one signal counted twice
# (the graph leg is seeded from the lexical top ranks), so corroboration
# comes from independent legs or two informative matched tokens.
r = run_hook({"FPL_MEM_PROMPT": "1"},
             json.dumps({"prompt": "can you check what time it is in "
                                   "tokyo right now", "cwd": root}))
report("an off-topic prompt injects nothing",
       r.returncode == 0 and r.stdout == "",
       "no informative overlap, no injection")
r = run_hook({"FPL_MEM_PROMPT": "1"}, "{not json")
report("junk hook input exits 0 with nothing",
       r.returncode == 0 and r.stdout == "", "hook cannot wedge a prompt")
r = run_hook({"FPL_MEM_PROMPT": "1", "FPL_DISABLE": "1"}, hook_json)
report("FPL_DISABLE short-circuits the prompt hook too",
       r.returncode == 0 and r.stdout == "", "kill switch honored")

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
