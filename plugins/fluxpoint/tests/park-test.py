#!/usr/bin/env python3
"""Park layer: a node nobody can run must not stop the ones that can.

Before this the engine had two answers for a node it could not complete —
halt the whole campaign, or drop the item and march on with a `null`. Real
deliveries guarantee the third case: 2-of-3 signing only humans can do, a
withdrawal only an external LP can perform, a 72h timelock. The property
under test is that such a node parks, says what would unblock it, marks the
run INCOMPLETE, and lets independent branches proceed — and that its
dependents inherit BLOCKED rather than being handed a null that reads like
a failure.

Executed, not inspected: every case runs the compiled graph under stubs and
counts the agent calls that actually happened.
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location(
    "compile_graph", os.path.join(PLUGIN, "scripts", "compile-graph.py"))
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
CONTRACTS = cg.load_contracts(os.path.join(PLUGIN, "contracts"))
RELEASE_PY = os.path.join(PLUGIN, "scripts", "release.py")
INBOX_PY = os.path.join(PLUGIN, "scripts", "inbox.py")
passed = failed = 0


def report(name, ok, detail):
    global passed, failed
    print(f"{'PASS' if ok else 'FAIL'}  {name:<54} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


def case(name, mutate, want):
    ir = copy.deepcopy(IR)
    mutate(ir)
    errs = cg.validate(ir, CONTRACTS)
    if want is None:
        report(name, not errs, "accepted" if not errs else f"rejected: {errs[:1]}")
    else:
        hit = any(want in e for e in errs)
        report(name, hit, f"rejected on '{want}'" if hit else f"NOT rejected ({errs})")


IR = {
    "version": 1,
    "name": "ceremony",
    "campaign": "vault genesis with a human signing step",
    "budget": {"maxNodes": 12},
    "nodes": [
        {"id": "prepare", "phase": "Prep", "prompt": "build the unsigned tx",
         "contract": "HarnessCheckV1"},
        {"id": "sign", "phase": "Ceremony", "actor": "human",
         "prompt": "2-of-3 hardware signing; no agent holds these keys",
         "contract": "HarnessCheckV1",
         "release": {"instructions": "Sign with 2 of the 3 hardware keys and paste "
                                     "the HarnessCheckV1 from cardano-cli.",
                     "proofContract": "HarnessCheckV1"},
         "wake": {"check": "cardano-cli query tip --mainnet", "everyMinutes": 30,
                  "deadline": "2030-01-01T00:00:00Z"}},
        {"id": "submit", "phase": "Ceremony", "after": "sign",
         "prompt": "submit the signed tx from {{prev}}", "contract": "HarnessCheckV1"},
        {"id": "sidework", "phase": "Parallel", "prompt": "update the runbook docs",
         "contract": "HarnessCheckV1"},
    ],
}

# ==================== validation ==========================================
case("a human node with a release block is accepted", lambda ir: None, None)
case("actor must be a known role",
     lambda ir: ir["nodes"][1].update(actor="robot"), "actor must be one of")
case("a human node with no release block",
     lambda ir: ir["nodes"][1].pop("release"), "needs a release block")
case("a release with no instructions",
     lambda ir: ir["nodes"][1]["release"].update(instructions="  "),
     "release.instructions required")
case("a proofContract that is not a contract",
     lambda ir: ir["nodes"][1]["release"].update(proofContract="NopeV1"),
     "not a known contract")
case("a proofContract that contradicts the node contract",
     lambda ir: ir["nodes"][1]["release"].update(proofContract="RedTeamV1"),
     "differs from the node's contract")
case("wake on an agent node",
     lambda ir: ir["nodes"][0].update(wake={"check": "true", "everyMinutes": 5}),
     "only meaningful with actor human")
case("wake with no predicate",
     lambda ir: ir["nodes"][1]["wake"].update(check=""), "wake.check required")
case("wake with a zero interval",
     lambda ir: ir["nodes"][1]["wake"].update(everyMinutes=0),
     "everyMinutes must be an integer >= 1")
case("a human node that also mutates",
     lambda ir: ir["nodes"][1].update(mutates=True), "cannot be combined with")
case("a misspelled release field",
     lambda ir: ir["nodes"][1]["release"].update(instuctions="typo"),
     "unknown field 'instuctions'")

# A parked node spawns nothing, so it must not be priced against the ceiling.
tight = copy.deepcopy(IR)
tight["budget"] = {"maxNodes": 3}
report("a parked node costs no agent calls", not cg.validate(tight, CONTRACTS),
       f"{cg.plan_node_count(tight)} planned for 3 agent nodes")


# ==================== execution ===========================================
def run(ir, args):
    js = cg.emit(ir, CONTRACTS)
    with tempfile.TemporaryDirectory() as d:
        out, spawned = os.path.join(d, "o.json"), os.path.join(d, "s.json")
        w = os.path.join(d, "w.mjs")
        with open(w, "w") as fh:
            fh.write(
                "import {writeFileSync} from 'node:fs';\n"
                "const SPAWNED=[];\n"
                "const agent=async(p)=>{SPAWNED.push(p);"
                "return {exit:0,command:'x',output:'ok'}};\n"
                "const parallel=async(t)=>Promise.all(t.map(f=>f()));\n"
                "const pipeline=async()=>[],log=()=>{},phase=()=>{};\n"
                f"const args={json.dumps(args)};\n"
                "const budget={total:null,spent:()=>0,remaining:()=>1e9};\n"
                "const workflow=0;\n"
                "(async () => {\n" + js.replace("export const meta", "const meta")
                + "\n})().then(r=>{"
                f"writeFileSync({json.dumps(out)},JSON.stringify(r||null));"
                f"writeFileSync({json.dumps(spawned)},JSON.stringify(SPAWNED))}})"
                ".catch(e=>{"
                f"writeFileSync({json.dumps(spawned)},JSON.stringify(SPAWNED));"
                "console.error(String(e&&e.message||e))})\n")
        p = subprocess.run(["node", w], capture_output=True, timeout=60, text=True)
        s = json.load(open(out)) if os.path.exists(out) else None
        c = json.load(open(spawned)) if os.path.exists(spawned) else []
        return s, c, p.stderr


summary, calls, err = run(IR, {"_releases": {}})
prov = {p["node"]: p["status"] for p in (summary or {}).get("provenance", [])}
report("the human node spawns no agent",
       not [c for c in calls if "hardware signing" in c], f"{len(calls)} spawn(s)")
report("the human node is reported BLOCKED", prov.get("sign") == "BLOCKED",
       prov.get("sign", f"absent; stderr={err[:40]}"))
report("its dependent inherits BLOCKED", prov.get("submit") == "BLOCKED",
       prov.get("submit", "absent"))
report("an independent branch still runs", prov.get("sidework") == "OK",
       prov.get("sidework", "absent"))
report("upstream work still ran", prov.get("prepare") == "OK",
       prov.get("prepare", "absent"))
report("the campaign does not halt", (summary or {}).get("outcome") == "INCOMPLETE",
       (summary or {}).get("outcome", "none"))
report("a blocked run can never read COMPLETE",
       (summary or {}).get("outcome") != "COMPLETE", (summary or {}).get("outcome"))
report("the wait is emitted with its predicate",
       bool([w for w in (summary or {}).get("waits", []) if w.get("check")]),
       str((summary or {}).get("waits"))[:60])
report("blocked ids ride out in the summary",
       set((summary or {}).get("blocked", [])) == {"sign", "submit"},
       str((summary or {}).get("blocked")))

# The instructions are the entire message the blocked human gets.
detail = [p.get("detail") for p in (summary or {}).get("provenance", [])
          if p.get("node") == "sign"]
report("the block carries its instructions",
       bool(detail) and "hardware keys" in (detail[0] or ""), str(detail)[:50])

# Released: the same graph, now with the proof on hand.
released = {"sign": {"node": "sign", "by": "nate",
                     "proof": {"exit": 0, "command": "cardano-cli", "output": "tx_ok"}}}
summary2, calls2, _ = run(IR, {"_releases": released})
prov2 = {p["node"]: p["status"] for p in (summary2 or {}).get("provenance", [])}
report("a released node reports RELEASED", prov2.get("sign") == "RELEASED",
       prov2.get("sign", "absent"))
report("the dependent runs once the block lifts", prov2.get("submit") == "OK",
       prov2.get("submit", "absent"))
report("the released campaign completes",
       (summary2 or {}).get("outcome") == "COMPLETE",
       (summary2 or {}).get("outcome"))
report("the pasted proof is what downstream consumes",
       ((summary2 or {}).get("results") or {}).get("sign", {}).get("output") == "tx_ok",
       str(((summary2 or {}).get("results") or {}).get("sign"))[:40])

# A missing releases map disarms the whole layer, so it must fail loudly.
summary3, calls3, err3 = run(IR, {})
report("no releases map -> the run throws",
       summary3 is None and "releases" in err3, (err3.strip()[:50] or "no error"))

# A graph with no parked nodes carries none of this machinery.
plain = copy.deepcopy(IR)
plain["nodes"] = [plain["nodes"][0], plain["nodes"][3]]
report("no park code without an actor", "BLOCKED" not in cg.emit(plain, CONTRACTS),
       "clean")

# ==================== release: proof, not an adjective ====================
with tempfile.TemporaryDirectory() as root:
    def rel(*a, stdin=""):
        return subprocess.run(
            [sys.executable, RELEASE_PY, "--root", root, "--plugin-root", PLUGIN, *a],
            input=stdin, capture_output=True, text=True)

    r = rel("--record", "--campaign", "c", "--node", "sign",
            "--contract", "HarnessCheckV1", stdin='"done"')
    report("a bare adjective is refused", r.returncode == 1,
           (r.stderr.strip().splitlines() or ["none"])[0][:50])

    r = rel("--record", "--campaign", "c", "--node", "sign",
            "--contract", "HarnessCheckV1", stdin='{"command":"x"}')
    report("a proof missing a required field is refused",
           r.returncode == 1 and "exit" in r.stderr, r.stderr.strip()[-40:])

    r = rel("--record", "--campaign", "c", "--node", "sign",
            "--contract", "HarnessCheckV1",
            stdin='{"exit":0,"command":"  ","tail":"x"}')
    report("a blank required string is refused",
           r.returncode == 1 and "blank proof" in r.stderr, r.stderr.strip()[-40:])

    r = rel("--record", "--campaign", "c", "--node", "sign",
            "--contract", "HarnessCheckV1",
            stdin='{"exit":0,"command":"cardano-cli submit","tail":"tx_abc"}')
    report("a well-formed proof is recorded", r.returncode == 0, r.stdout.strip()[:50])

    r = rel("--load", "--campaign", "c")
    loaded = json.loads(r.stdout)
    report("the release loads back keyed by node", "sign" in loaded,
           str(list(loaded))[:30])
    report("the recorded proof is the pasted document",
           loaded.get("sign", {}).get("proof", {}).get("tail") == "tx_abc",
           str(loaded.get("sign", {}).get("proof"))[:40])

    r = rel("--load", "--campaign", "a different campaign")
    report("another campaign's releases are not consulted", r.stdout.strip() == "{}",
           r.stdout.strip()[:20])

# ==================== inbox ==============================================
with tempfile.TemporaryDirectory() as root:
    def ib(*a):
        return subprocess.run([sys.executable, INBOX_PY, "--root", root, *a],
                              capture_output=True, text=True)

    report("an empty inbox counts zero", ib("--count").stdout.strip() == "0", "0")
    ib("--add", "--kind", "blocked", "--node", "sign", "--campaign", "c",
       "--detail", "2-of-3 signing")
    report("a block raises an item", ib("--count").stdout.strip() == "1", "1")
    ib("--add", "--kind", "blocked", "--node", "sign", "--campaign", "c",
       "--detail", "2-of-3 signing")
    report("the same block twice is one item, not two",
           ib("--count").stdout.strip() == "1", ib("--count").stdout.strip())
    r = ib("--list")
    report("--list says what is waiting and how to clear it",
           "2-of-3 signing" in r.stdout and "--resolve" in r.stdout,
           r.stdout.strip().splitlines()[0][:40])
    ib("--resolve", "blocked:c:sign")
    report("resolving closes it", ib("--count").stdout.strip() == "0", "0")
    report("a resolved item stays in the log",
           len([l for l in open(os.path.join(root, ".claude", "fluxpoint",
                                             "inbox.jsonl"))]) == 2, "2 rows")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
