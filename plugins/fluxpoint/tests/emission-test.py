#!/usr/bin/env python3
"""Emission coverage: every accepted IR field must demonstrably do something.

The recurring defect in this compiler was not a wrong output, it was a
*silent* one: a field the validator accepted, the docs described, and the
emitter ignored. `verify: harness` was priced into the budget and emitted
nothing. `haltWhen` on a fan-out node compiled clean and could never fire.
A misspelled `verifyOver` disabled verification while the spec still
claimed it.

Reviewing for that is unreliable, so this test makes it mechanical. For
every field in the compiler's own registries, it sets the field to a
non-default value and asserts the compiler's output changes — in the
emitted JS, in the planned agent count, or in validation. A field that
changes none of the three is inert, and inert is exactly the bug.

The completeness half matters as much: any field added to a registry
without a probe here fails the run, so the next inert field cannot be
introduced quietly.
"""
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location(
    "compile_graph", os.path.join(PLUGIN, "scripts", "compile-graph.py")
)
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
CONTRACTS = cg.load_contracts(os.path.join(PLUGIN, "contracts"))

passed = failed = 0


def result_of(ir):
    """Everything the compiler produces, as a comparable tuple."""
    errs = cg.validate(ir, CONTRACTS)
    if errs:
        return ("INVALID", tuple(errs), None, None)
    return ("VALID", (), cg.plan_node_count(ir), cg.emit(ir, CONTRACTS))


def probe(level, field, base, mutate, note=""):
    """Assert that setting `field` changes what the compiler produces."""
    global passed, failed
    before = result_of(copy.deepcopy(base))
    after_ir = copy.deepcopy(base)
    mutate(after_ir)
    after = result_of(after_ir)

    what = []
    if before[0] != after[0] or before[1] != after[1]:
        what.append("validation")
    if before[2] != after[2]:
        what.append("plan")
    if before[3] != after[3]:
        what.append("emission")

    ok = bool(what)
    label = f"{level}.{field}"
    detail = "+".join(what) if ok else "NOTHING — field is inert"
    suffix = f"  ({note})" if note and not ok else ""
    print(f"{'PASS' if ok else 'FAIL'}  {label:<26} -> {detail}{suffix}")
    passed, failed = (passed + ok, failed + (not ok))
    return ok


# --------------------------------------------------------------- baselines
NODE_BASE = {
    "version": 1,
    "name": "base",
    "campaign": "a baseline campaign used to probe field effects",
    "budget": {"maxNodes": 400},
    "roles": {"other": {"effort": "high"}},
    "lists": {"la": [{"key": "a"}, {"key": "b"}], "lb": [{"key": "z"}]},
    "nodes": [
        {"id": "seed", "phase": "P0", "prompt": "seed", "contract": "DesignV1"},
        {
            "id": "probe",
            "phase": "P1",
            "prompt": "do the thing",
            "contract": "FindingsV1",
        },
    ],
}
N = 1  # index of the node under probe


def setnode(**kw):
    def m(ir):
        ir["nodes"][N].update(kw)
    return m


# ------------------------------------------------------------- node fields
probe("node", "id", NODE_BASE, setnode(id="renamed"))
probe("node", "phase", NODE_BASE, setnode(phase="Elsewhere"))
probe("node", "prompt", NODE_BASE, setnode(prompt="a materially different prompt"))
probe("node", "contract", NODE_BASE, setnode(contract="DesignV1"))
probe("node", "role", NODE_BASE, setnode(role="other"))
probe("node", "effort", NODE_BASE, setnode(effort="max"))
probe("node", "model", NODE_BASE, setnode(model="claude-haiku-4-5-20251001"))
probe("node", "agentType", NODE_BASE, setnode(agentType="red-team-reviewer"))
probe("node", "foreach", NODE_BASE, setnode(foreach="la"))
probe("node", "after", NODE_BASE,
      setnode(after="seed", prompt="use the prior result: {{prev}}"))
probe("node", "mutates", NODE_BASE, lambda ir: (
    ir["nodes"][N].update(mutates=True),
    ir["nodes"].append({"id": "gate", "phase": "G", "prompt": "verify it",
                        "contract": "HarnessCheckV1", "independent": True,
                        "verifies": "probe"}),
))
probe("node", "independent", NODE_BASE, setnode(independent=True, verifies="seed"))
probe("node", "verifies", NODE_BASE, setnode(independent=True, verifies="seed"))
probe("node", "verify", NODE_BASE, setnode(verify="panel:3", verifyOver="findings"))
probe("node", "verifyOver", NODE_BASE, setnode(verify="skeptic:1", verifyOver="findings"))
probe("node", "expectItems", NODE_BASE,
      setnode(verify="skeptic:1", verifyOver="findings", expectItems=9))
probe("node", "haltWhen", NODE_BASE,
      setnode(contract="HarnessCheckV1", haltWhen="exit != 0"))
probe("node", "haltReason", NODE_BASE,
      setnode(contract="HarnessCheckV1", haltWhen="exit != 0",
              haltReason="a specific and quotable reason"))
probe("node", "onRed", NODE_BASE, setnode(onRed="drop+log"))
probe("node", "isolation", NODE_BASE, setnode(isolation="worktree"))
probe("node", "repeat", NODE_BASE,
      setnode(verify="skeptic:1", verifyOver="findings",
              repeat={"untilDryRounds": 2, "maxRounds": 6, "dedupeBy": ["file"]}))


def _irreversible(ir):
    """Mark the probe node irreversible, with the scaffolding it demands.

    Probed against a graph that stays VALID on purpose: a rejection would
    also register as "changed", and would prove the validator moved without
    proving a single line of the once-only guard is emitted.
    """
    node = ir["nodes"][N]
    ir["requiredArgs"] = ["confirm"]
    ir["nodes"][:0] = [
        {"id": "dry", "phase": "P0", "prompt": "rehearse without submitting",
         "contract": "HarnessCheckV1"},
        {"id": "gate", "phase": "P0", "prompt": "independently re-derive {{prev}}",
         "after": "dry", "contract": "HarnessCheckV1", "independent": True,
         "verifies": "dry", "haltWhen": "exit != 0"},
    ]
    node["irreversible"] = True


probe("node", "irreversible", NODE_BASE, _irreversible)


def _human(ir):
    ir["nodes"][N].update(actor="human", release={
        "instructions": "a person signs this one",
        "proofContract": ir["nodes"][N]["contract"]})


probe("node", "actor", NODE_BASE, _human)

# release and wake are probed against a base that is already parked, so the
# change measured is theirs rather than actor's leaking into both.
PARK_BASE = copy.deepcopy(NODE_BASE)
_human(PARK_BASE)
WAKE = {"check": "cardano-cli query tip --mainnet", "everyMinutes": 30}
probe("node", "release", PARK_BASE,
      lambda ir: ir["nodes"][N]["release"].update(
          instructions="materially different instructions for the blocked human"))
probe("node", "wake", PARK_BASE,
      lambda ir: ir["nodes"][N].update(wake=copy.deepcopy(WAKE)))

# ---------------------------------------------------- release / wake fields
probe("release", "instructions", PARK_BASE,
      lambda ir: ir["nodes"][N]["release"].update(
          instructions="the exact text the blocked human will read"))
# proofContract must agree with the node's contract, so moving it alone is
# a compile error — which is the effect, and the reason the field is not
# merely decorative.
probe("release", "proofContract", PARK_BASE,
      lambda ir: ir["nodes"][N]["release"].update(proofContract="DesignV1"))

WAKE_BASE = copy.deepcopy(PARK_BASE)
WAKE_BASE["nodes"][N]["wake"] = copy.deepcopy(WAKE)
probe("wake", "check", WAKE_BASE,
      lambda ir: ir["nodes"][N]["wake"].update(check="cardano-cli query utxo --mainnet"))
probe("wake", "everyMinutes", WAKE_BASE,
      lambda ir: ir["nodes"][N]["wake"].update(everyMinutes=180))
probe("wake", "deadline", WAKE_BASE,
      lambda ir: ir["nodes"][N]["wake"].update(deadline="2030-06-01T00:00:00Z"))

# ----------------------------------------------------------- repeat fields
REPEAT_BASE = copy.deepcopy(NODE_BASE)
REPEAT_BASE["nodes"][N].update(
    verify="skeptic:1", verifyOver="findings",
    repeat={"untilDryRounds": 2, "maxRounds": 6, "dedupeBy": ["file"]},
)


def setrepeat(**kw):
    def m(ir):
        ir["nodes"][N]["repeat"].update(kw)
    return m


probe("repeat", "untilDryRounds", REPEAT_BASE, setrepeat(untilDryRounds=3))
probe("repeat", "maxRounds", REPEAT_BASE, setrepeat(maxRounds=9))
probe("repeat", "dedupeBy", REPEAT_BASE, setrepeat(dedupeBy=["file", "line"]))

# ----------------------------------------------------------- budget fields
probe("budget", "maxNodes", NODE_BASE, lambda ir: ir["budget"].update(maxNodes=1))
probe("budget", "verifyFloorTokens", REPEAT_BASE,
      lambda ir: ir["budget"].update(verifyFloorTokens=123456))
probe("budget", "nodeFloorTokens", NODE_BASE,
      lambda ir: ir["budget"].update(nodeFloorTokens=654321))

# ------------------------------------------------------- top-level IR fields
probe("IR", "version", NODE_BASE, lambda ir: ir.update(version=2))
probe("IR", "name", NODE_BASE, lambda ir: ir.update(name="a-different-name"))
probe("IR", "campaign", NODE_BASE, lambda ir: ir.update(campaign="a different campaign line"))
# maxNodes=1 is below the 2 nodes this baseline plans, so the ceiling bites.
probe("IR", "budget", NODE_BASE, lambda ir: ir.update(budget={"maxNodes": 1}))
probe("IR", "defaults", NODE_BASE, lambda ir: ir.update(defaults={"effort": "max"}))
probe("IR", "roles", NODE_BASE,
      lambda ir: (ir["roles"].update(extra={"agentType": "graph-auditor"}),
                  ir["nodes"][N].update(role="extra")))
probe("IR", "lists", NODE_BASE,
      lambda ir: (ir["lists"].update(la=[{"key": "a"}, {"key": "b"}, {"key": "c"}]),
                  ir["nodes"][N].update(foreach="la")))
probe("IR", "nodes", NODE_BASE,
      lambda ir: ir["nodes"].append({"id": "extra", "phase": "P2",
                                     "prompt": "another", "contract": "DesignV1"}))
probe("IR", "requiredArgs", NODE_BASE, lambda ir: ir.update(requiredArgs=["target"]))
probe("IR", "argDefaults", NODE_BASE, lambda ir: ir.update(argDefaults={"target": "HEAD"}))

# ------------------------------------------------------------ completeness
print()
probed = {
    "node": {
        "id", "phase", "prompt", "contract", "role", "effort", "model", "agentType",
        "foreach", "after", "mutates", "independent", "verifies", "verify",
        "verifyOver", "expectItems", "haltWhen", "haltReason", "onRed",
        "isolation", "repeat", "irreversible", "actor", "release", "wake",
    },
    "repeat": {"untilDryRounds", "maxRounds", "dedupeBy"},
    "release": {"instructions", "proofContract"},
    "wake": {"check", "everyMinutes", "deadline"},
    "budget": {"maxNodes", "verifyFloorTokens", "nodeFloorTokens"},
    "IR": {
        "version", "name", "campaign", "budget", "defaults", "roles", "lists",
        "nodes", "requiredArgs", "argDefaults",
    },
}
for level, registry in [
    ("node", cg.NODE_FIELDS), ("repeat", cg.REPEAT_FIELDS),
    ("release", cg.RELEASE_FIELDS), ("wake", cg.WAKE_FIELDS),
    ("budget", cg.BUDGET_FIELDS), ("IR", cg.IR_FIELDS),
]:
    missing = registry - probed[level]
    stale = probed[level] - registry
    ok = not missing and not stale
    detail = "every field probed"
    if missing:
        detail = f"NO PROBE for {sorted(missing)} — add one before shipping the field"
    elif stale:
        detail = f"probes for fields no longer in the registry: {sorted(stale)}"
    print(f"{'PASS' if ok else 'FAIL'}  completeness: {level:<12} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))

# Removed tiers must stay removed, with a message that points somewhere.
rej = cg.validate(
    {**copy.deepcopy(NODE_BASE), "nodes": [
        {"id": "x", "phase": "P", "prompt": "p", "contract": "HarnessCheckV1",
         "verify": "harness"}]},
    CONTRACTS,
)
ok = any("was removed" in r and "independent" in r for r in rej)
print(f"{'PASS' if ok else 'FAIL'}  {'harness tier stays removed':<26} -> "
      f"{'rejected with a pointer to mutates/independent' if ok else f'got {rej}'}")
passed, failed = (passed + ok, failed + (not ok))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
