#!/usr/bin/env python3
"""Lessons: what a campaign established, kept where the next one reads it.

Two properties carry this layer, and both are easy to get subtly wrong:

  * The killed half is filed. A panel's rejects and the objections behind
    them are the expensive part of a sweep, and `verifyItems` used to drop
    them on the floor — so the next sweep re-found the item and paid a fresh
    panel to reach a verdict that already existed.
  * A seed is advisory and nothing more. It reaches a prompt; it never
    reaches the dedup set. Seeding the dedup set would silently discard a
    re-found item, which is exactly how a stale lesson hides a live
    regression: the finder reports it, the loop drops it as already-known,
    and the sweep reads clean.

Executed, not inspected: the compiled graph runs under stubs and the rows
that come out are the assertions.
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
MEMORY_PY = os.path.join(PLUGIN, "scripts", "memory.py")
RECORD_PY = os.path.join(PLUGIN, "scripts", "record-run.py")

spec = importlib.util.spec_from_file_location(
    "compile_graph", os.path.join(PLUGIN, "scripts", "compile-graph.py"))
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
CONTRACTS = cg.load_contracts(os.path.join(PLUGIN, "contracts"))

passed = failed = 0


def report(name, ok, detail):
    global passed, failed
    print(f"{'PASS' if ok else 'FAIL'}  {name:<56} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


IR = {
    "version": 1, "name": "sweep", "campaign": "audit the validators for real bugs",
    "budget": {"maxNodes": 80},
    "nodes": [{
        "id": "find", "phase": "Find", "contract": "FindingsV1",
        "prompt": "find bugs; already surfaced: {{seen}}",
        "verify": "panel:3", "verifyOver": "findings",
        "repeat": {"untilDryRounds": 2, "maxRounds": 4, "dedupeBy": ["file", "claim"]},
        "memory": {"seed": "audit", "emit": "audit", "classBy": None},
    }],
}


def case(name, mutate, want):
    ir = copy.deepcopy(IR)
    mutate(ir)
    errs = cg.validate(ir, CONTRACTS)
    if want is None:
        report(name, not errs, "accepted" if not errs else f"rejected: {errs[:1]}")
    else:
        hit = any(want in e for e in errs)
        report(name, hit, f"rejected on '{want}'" if hit else f"NOT rejected ({errs})")


# ==================== validation ==========================================
case("a seeding, emitting discovery node compiles", lambda ir: None, None)
case("an unknown memory field",
     lambda ir: ir["nodes"][0]["memory"].update(emitt="typo"),
     "unknown field 'emitt'")
case("a memory block that does neither",
     lambda ir: ir["nodes"][0].update(memory={}),
     "declares neither seed nor emit")
case("a tag that cannot be referenced",
     lambda ir: ir["nodes"][0]["memory"].update(emit="Audit Tag"),
     "lowercase kebab-case tag")
case("seeding a node that runs once",
     lambda ir: (ir["nodes"][0].pop("repeat"),
                 ir["nodes"][0].update(prompt="find bugs")),
     "nowhere to put it")
case("emitting without a verification tier",
     lambda ir: ir["nodes"][0].update(verify="schema-only"),
     "promote a well-formed guess")
case("emitting from a contract whose items carry no claim",
     lambda ir: (ir["nodes"][0].update(contract="RedTeamV1", verifyOver="findings"),
                 ir["nodes"][0]["repeat"].update(dedupeBy=["finding"])),
     "items to carry a 'claim'")
case("two spellings of one dedupe key",
     lambda ir: ir["nodes"][0]["memory"].update(key=["file"]),
     "already declared as repeat.dedupeBy")
case("a priors-declaring sweep compiles",
     lambda ir: ir["nodes"][0]["memory"].update(priors=True), None)
case("priors that is anything but true",
     lambda ir: ir["nodes"][0]["memory"].update(priors=5),
     "must be literally true")
case("priors with no seed to load them from",
     lambda ir: ir["nodes"][0]["memory"].update(seed=None, priors=True)
     or ir["nodes"][0]["memory"].pop("seed"),
     "without memory.seed")
case("priors with no panel to tell",
     lambda ir: (ir["nodes"][0].pop("verify"),
                 ir["nodes"][0].pop("verifyOver"),
                 ir["nodes"][0].update(memory={"seed": "audit",
                                               "priors": True})),
     "nobody to tell")

# A one-shot node may emit, but then its identity has to be declared.
ONESHOT = copy.deepcopy(IR)
ONESHOT["nodes"][0].pop("repeat")
ONESHOT["nodes"][0]["prompt"] = "find bugs"
ONESHOT["nodes"][0]["memory"] = {"emit": "audit", "classBy": None}
errs = cg.validate(copy.deepcopy(ONESHOT), CONTRACTS)
report("a one-shot emitter needs an explicit key",
       any("cannot be implicit" in e for e in errs), (errs or ["none"])[0][:56])
keyed = copy.deepcopy(ONESHOT)
keyed["nodes"][0]["memory"]["key"] = ["file", "claim"]
report("and compiles once it has one", not cg.validate(keyed, CONTRACTS),
       str(cg.validate(keyed, CONTRACTS))[:50] or "accepted")
badkey = copy.deepcopy(keyed)
badkey["nodes"][0]["memory"]["key"] = ["nonexistent"]
report("a key that is not a field of the item is rejected",
       any("not a field of" in e for e in cg.validate(badkey, CONTRACTS)), "rejected")

# The machinery must not appear in a graph that never asked for it.
plain = copy.deepcopy(IR)
plain["nodes"][0].pop("memory")
plain["nodes"][0]["memory"] = None
plain["nodes"][0].pop("memory")
report("no memory machinery without a memory block",
       "MEMORY" not in cg.emit(copy.deepcopy(plain), CONTRACTS, {}), "clean")


# ==================== execution ===========================================
def run(ir, args, findings):
    """Run the compiled graph under stubs. `findings` is what the finder
    returns each round, and every refuter kills the item whose file is
    'killme.ak' — so both halves of the panel's output are exercised."""
    js = cg.emit(ir, CONTRACTS, {})
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "o.json")
        prompts = os.path.join(d, "p.json")
        w = os.path.join(d, "w.mjs")
        with open(w, "w") as fh:
            fh.write(
                "import {writeFileSync} from 'node:fs';\n"
                "const PROMPTS=[];\n"
                f"const ROUNDS={json.dumps(findings)};\n"
                "let round=0;\n"
                "const agent=async(p,o)=>{PROMPTS.push(p);\n"
                "  if(String(o.label||'').includes('refute'))\n"
                "    return {refuted: p.includes('killme.ak'),\n"
                "            reason: p.includes('killme.ak')\n"
                "              ? 'a guard upstream already forbids this state'\n"
                "              : 'could not find a reason it is wrong'};\n"
                "  return {findings: ROUNDS[round++] || []}};\n"
                "const parallel=async(t)=>Promise.all(t.map(f=>f()));\n"
                "const pipeline=async()=>[],log=()=>{},phase=()=>{};\n"
                f"const args={json.dumps(args)};\n"
                "const budget={total:null,spent:()=>0,remaining:()=>1e9};\n"
                "const workflow=0;\n"
                "(async () => {\n" + js.replace("export const meta", "const meta")
                + "\n})().then(r=>{"
                f"writeFileSync({json.dumps(out)},JSON.stringify(r||null));"
                f"writeFileSync({json.dumps(prompts)},JSON.stringify(PROMPTS))}})"
                ".catch(e=>{console.error(String(e&&e.message||e));"
                f"writeFileSync({json.dumps(prompts)},JSON.stringify(PROMPTS))}})\n")
        p = subprocess.run(["node", w], capture_output=True, timeout=90, text=True)
        s = json.load(open(out)) if os.path.exists(out) else None
        pr = json.load(open(prompts)) if os.path.exists(prompts) else []
        return s, pr, p.stderr


REAL = {"file": "vault.ak", "line": 12, "severity": "HIGH",
        "claim": "the datum is trusted without checking the output address",
        "failure_path": "an attacker supplies a datum naming their own address"}
DEAD = {"file": "killme.ak", "line": 3, "severity": "LOW",
        "claim": "this comparison looks inverted on a first reading of it",
        "failure_path": "no reachable state produces the inverted branch here"}

summary, prompts, err = run(IR, {}, [[REAL, DEAD], [], []])
mem = {(m["tag"], m["dedupeKey"]): m for m in (summary or {}).get("memory", [])}
report("the run emits lessons at all", bool(mem),
       f"{len(mem)} lesson(s)" if mem else f"none; stderr={err[:50]}")
surviving = [m for m in mem.values() if m["status"] == "surviving"]
killed = [m for m in mem.values() if m["status"] == "killed"]
report("a survivor is filed as surviving",
       len(surviving) == 1 and surviving[0]["claim"] == REAL["claim"],
       str([m["status"] for m in mem.values()]))
report("and the panel's kill is filed too, not dropped",
       len(killed) == 1 and killed[0]["claim"] == DEAD["claim"],
       f"{len(killed)} killed")
report("the kill carries the objection that produced it",
       bool(killed) and "guard upstream" in (killed[0].get("objection") or ""),
       (killed[0].get("objection") if killed else "none") or "empty")
report("identity is built from the node's own dedupe fields",
       ("audit", "vault.ak|" + REAL["claim"]) in mem,
       str(list(mem))[:60])
report("a first sweep reports seeding nothing",
       (summary or {}).get("memorySeeded", {}).get("audit") == 0,
       str((summary or {}).get("memorySeeded")))

# Seeded: the finder is told where the frontier was.
seed = {"audit": {"keys": ["vault.ak|" + REAL["claim"]],
                  "killed": [{"claim": DEAD["claim"], "objection": "upstream guard"}]}}
summary2, prompts2, _ = run(IR, {"_seen": seed}, [[], []])
report("a seeded sweep says how much ground it started on",
       (summary2 or {}).get("memorySeeded", {}).get("audit") == 1,
       str((summary2 or {}).get("memorySeeded")))
report("the seed reaches the finder's prompt",
       any("vault.ak|" + REAL["claim"] in p for p in prompts2),
       "in the prompt" if prompts2 else "no prompts")

# The safety property: a seeded key must not suppress a re-found item.
summary3, _, _ = run(IR, {"_seen": seed}, [[REAL], [], []])
kept3 = [f["claim"] for f in ((summary3 or {}).get("results", {}).get("find") or [])]
report("a re-found item is NOT suppressed by its seed",
       REAL["claim"] in kept3,
       f"{len(kept3)} kept" if kept3 else "SUPPRESSED — a stale lesson could hide a regression")

# Priors: the killed half rides into the REFUTER prompts — the finder still
# looks everywhere, but the panel is told what earlier panels killed and why,
# framed as priors it may overturn.
PRI_IR = copy.deepcopy(IR)
PRI_IR["nodes"][0]["memory"]["priors"] = True
_, prompts4, _ = run(PRI_IR, {"_seen": seed}, [[REAL], [], []])
ref4 = [p for p in prompts4 if "Attempt to REFUTE" in p]
report("killed priors reach the refuter prompts",
       ref4 and all("killed because: upstream guard" in p for p in ref4),
       f"{len(ref4)} refuter prompt(s) carry the objection"
       if ref4 else "no refuter prompts captured")
report("priors are framed as overturnable, not as verdicts",
       ref4 and all("priors, not verdicts" in p for p in ref4),
       "framing present" if ref4 else "no refuter prompts")
find4 = [p for p in prompts4 if "Attempt to REFUTE" not in p]
report("the finder's prompt carries the frontier, never the priors",
       find4 and not any("killed because:" in p for p in find4),
       "finder prompts clean")
_, prompts5, _ = run(PRI_IR, {}, [[REAL], [], []])
ref5 = [p for p in prompts5 if "Attempt to REFUTE" in p]
report("an unseeded priors run adds nothing to the prompt",
       ref5 and not any("priors, not verdicts" in p for p in ref5),
       "empty priors are silent")

# ==================== the store ===========================================
def mem_py(root, *a, stdin=""):
    return subprocess.run([sys.executable, MEMORY_PY, "--root", root, *a],
                          input=stdin, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as root:
    runs = os.path.join(root, ".claude", "fluxpoint", "runs")
    os.makedirs(runs)
    payload = json.dumps({"memory": (summary or {}).get("memory", [])})

    r = mem_py(root, "--append", "--run-id", "wf-ghost", "--state-dir", runs,
               stdin=payload)
    report("a lesson whose run was never recorded is refused",
           "no run artifact" in r.stderr and not os.path.exists(
               os.path.join(root, ".claude", "fluxpoint", "memory.jsonl")),
           r.stderr.strip()[-46:] or "filed anyway")

    json.dump({"runId": "wf-1"}, open(os.path.join(runs, "wf-1.json"), "w"))
    r = mem_py(root, "--append", "--run-id", "wf-1", "--state-dir", runs, stdin=payload)
    report("lessons from a recorded run are filed", "filed 2 lesson(s)" in r.stdout,
           r.stdout.strip()[:50])

    r = mem_py(root, "--load", "--tag", "audit")
    loaded = json.loads(r.stdout)
    report("the seed map loads back keyed by tag",
           len(loaded.get("audit", {}).get("keys", [])) == 2, str(loaded)[:50])
    report("and carries the killed claims with their objections",
           len(loaded["audit"]["killed"]) == 1
           and "guard upstream" in loaded["audit"]["killed"][0]["objection"],
           str(loaded["audit"]["killed"])[:50])

    r = mem_py(root, "--load", "--tag", "other-sweep")
    report("another tag's lessons are not consulted",
           json.loads(r.stdout) == {"other-sweep": {"keys": [], "killed": []}},
           r.stdout.strip()[:40])

    # Re-filing the same identity from a later run supersedes rather than edits.
    json.dump({"runId": "wf-2"}, open(os.path.join(runs, "wf-2.json"), "w"))
    mem_py(root, "--append", "--run-id", "wf-2", "--state-dir", runs, stdin=payload)
    rows = [json.loads(l) for l in
            open(os.path.join(root, ".claude", "fluxpoint", "memory.jsonl")) if l.strip()]
    report("re-finding appends rather than rewriting", len(rows) == 4, f"{len(rows)} rows")
    report("the newer row names what it replaced",
           all(r.get("supersededBy") == "wf-2" for r in rows[2:]), "superseded")
    r = mem_py(root, "--load", "--tag", "audit")
    report("but the working set stays one row per identity",
           len(json.loads(r.stdout)["audit"]["keys"]) == 2, "2 keys")

    # A half-written lesson must never reach the store.
    bad = json.dumps({"memory": [{"tag": "audit", "dedupeKey": "k",
                                  "claim": "too short", "status": "surviving",
                                  "node": "find"}]})
    r = mem_py(root, "--append", "--run-id", "wf-2", "--state-dir", runs, stdin=bad)
    report("a malformed lesson is refused, not filed",
           "skipped a malformed lesson" in r.stderr and r.returncode == 1,
           r.stderr.strip()[-40:])

    with open(os.path.join(root, ".claude", "fluxpoint", "memory.jsonl"), "a") as fh:
        fh.write("not json\n")
    r = mem_py(root, "--list")
    report("a corrupted store fails hard rather than shrinking",
           "is not valid JSON" in (r.stderr + r.stdout), (r.stderr or r.stdout)[:46])

# ==================== record-run files them end to end ===================
with tempfile.TemporaryDirectory() as root:
    runs = os.path.join(root, ".claude", "fluxpoint", "runs")
    full = dict(summary or {})
    full["provenance"] = [{"node": "find", "status": "OK", "detail": ""}]
    full["outcome"] = "COMPLETE"
    open(os.path.join(root, "WORK.md"), "w").write(
        "# w\n\n## Evidence\n\n| When (UTC) | Source | Outcome | Claim | Proof |\n"
        "|---|---|---|---|---|\n")
    r = subprocess.run(
        [sys.executable, RECORD_PY, "--run-id", "wf-9", "--graph",
         os.path.join(root, "WORK.md"), "--root", root, "--state-dir", runs],
        input=json.dumps(full), capture_output=True, text=True)
    report("record-run files the run's lessons", "filed 2 lesson(s)" in r.stdout,
           r.stdout.strip().splitlines()[-1][:50] if r.stdout else r.stderr[:50])
    row = [l for l in open(os.path.join(root, "WORK.md")) if l.startswith("| 20")]
    report("the Evidence row distinguishes a seeded sweep from a cold one",
           bool(row) and "memory:" in row[0] and "filed 2 lesson(s)" in row[0],
           (row[0][-64:].strip() if row else "no row"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
