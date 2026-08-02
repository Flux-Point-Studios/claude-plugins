#!/usr/bin/env python3
"""Invariant tests for the fluxpoint graph compiler.

Each case asserts that a specific class of unsound graph is REJECTED at
compile time, and that the canonical sound graph compiles. Run:
  python3 plugins/fluxpoint/tests/compile-test.py
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN, "scripts"))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "compile_graph", os.path.join(PLUGIN, "scripts", "compile-graph.py")
)
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)

CONTRACTS = cg.load_contracts(os.path.join(PLUGIN, "contracts"))

BASE = {
    "version": 1,
    "name": "t",
    "campaign": "a campaign that exists to be validated",
    "budget": {"maxNodes": 32},
    "lists": {"dims": [{"key": "a", "brief": "x"}]},
    "nodes": [
        {
            "id": "find",
            "phase": "Find",
            "foreach": "dims",
            "prompt": "review {{item.brief}}",
            "contract": "FindingsV1",
            "verify": "panel:3",
            "verifyOver": "findings",
            "expectItems": 2,
        }
    ],
}

passed = failed = 0


def case(name, mutate, want_reject_substr):
    """want_reject_substr=None means the IR must be ACCEPTED."""
    global passed, failed
    ir = copy.deepcopy(BASE)
    mutate(ir)
    findings = cg.validate(ir, CONTRACTS)
    if want_reject_substr is None:
        ok = not findings
        detail = "accepted" if ok else f"rejected: {findings}"
    else:
        ok = any(want_reject_substr in f for f in findings)
        detail = f"rejected on '{want_reject_substr}'" if ok else f"NOT rejected (got {findings})"
    print(f"{'PASS' if ok else 'FAIL'}  {name:<54} -> {detail}")
    passed, failed = (passed + ok, failed + (not ok))


# The canonical sound graph must compile and emit valid-looking JS.
case("sound graph accepted", lambda ir: None, None)

# Contract layer.
case("node without a contract", lambda ir: ir["nodes"][0].pop("contract"), "contract required")
case("unknown contract name", lambda ir: ir["nodes"][0].update(contract="NopeV9"), "unknown contract")
case(
    "verifyOver names a non-field",
    lambda ir: ir["nodes"][0].update(verifyOver="nope"),
    "is not a field",
)

# Verification layer — the bug that shipped in v0.1.
def add_mutator(ir):
    ir["nodes"].append(
        {
            "id": "build",
            "phase": "Build",
            "prompt": "implement it",
            "contract": "SliceV1",
            "mutates": True,
        }
    )


case("mutator with no independent verifier", add_mutator, "may not certify its own work")


def add_verified_mutator(ir):
    add_mutator(ir)
    ir["nodes"].append(
        {
            "id": "gate",
            "phase": "Gate",
            "after": "build",
            "prompt": "re-run the harness yourself",
            "contract": "HarnessCheckV1",
            "independent": True,
            "verifies": "build",
            "haltWhen": "exit != 0",
        }
    )


case("mutator WITH independent verifier", add_verified_mutator, None)


def self_verify(ir):
    add_mutator(ir)
    ir["nodes"][1].update(independent=True, verifies="build")


case("node verifying itself", self_verify, "cannot verify itself")


def verifier_not_independent(ir):
    add_verified_mutator(ir)
    ir["nodes"][2].pop("independent")


case("verifier not marked independent", verifier_not_independent, "not marked independent")

# Panel sanity.
case("even panel (majority undefined)", lambda ir: ir["nodes"][0].update(verify="panel:4"), "even")
case("bogus tier", lambda ir: ir["nodes"][0].update(verify="vibes"), "verify must be")
case(
    "panel without verifyOver",
    lambda ir: ir["nodes"][0].pop("verifyOver"),
    "needs verifyOver",
)

# Edge layer.
case(
    "after references unknown node",
    lambda ir: ir["nodes"][0].update(after="ghost"),
    "is not a node defined earlier",
)
case(
    "foreach references unknown list",
    lambda ir: ir["nodes"][0].update(foreach="ghosts"),
    "has no entry under lists",
)
case(
    "role references undeclared role",
    lambda ir: ir["nodes"][0].update(role="ghost"),
    "is not declared under roles",
)

# Budget layer.
case("fan-out exceeds budget", lambda ir: ir["budget"].update(maxNodes=3), "budget.maxNodes is 3")
case("no budget ceiling", lambda ir: ir.pop("budget"), "budget.maxNodes: required")

# Halt grammar.
case(
    "malformed haltWhen",
    lambda ir: ir["nodes"][0].update(haltWhen="if it feels wrong"),
    "haltWhen must be",
)

# A declared verification tier must actually run on a single (non-foreach)
# node too. It silently did not before v1.0.0 — the tier was emitted into the
# prelude and never called, so the claims went unverified while the spec said
# they were checked.
single = copy.deepcopy(BASE)
single["nodes"] = [
    {
        "id": "solo",
        "phase": "Solo",
        "prompt": "find things",
        "contract": "FindingsV1",
        "verify": "skeptic:1",
        "verifyOver": "findings",
        "expectItems": 2,
    }
]
single_js = cg.emit(single, CONTRACTS)
for needle, why in [
    ("verifyItems(", "single node calls the verifier"),
    ('"findings"', "verifier is pointed at the contract field"),
]:
    ok = needle in single_js
    print(f"{'PASS' if ok else 'FAIL'}  single-node tier: {why:<38} -> {'found' if ok else 'MISSING'}")
    passed, failed = (passed + ok, failed + (not ok))

# Emission smoke: the sound graph produces JS containing its guarantees.
js = cg.emit(BASE, CONTRACTS)
for needle, why in [
    ("typeof args === 'object'", "args normalizer present"),
    ("VERIFY_FLOOR", "budget floor present"),
    ("Attempt to REFUTE", "refuters attack, never confirm"),
    ("kills < need", "survival is decided by the majority threshold"),
    ('"Find", 3, 2)', "panel:3 passes need=2, a true majority"),
    ("PROVENANCE", "provenance recorded"),
]:
    ok = needle in js
    print(f"{'PASS' if ok else 'FAIL'}  emitted JS: {why:<42} -> {'found' if ok else 'MISSING'}")
    passed, failed = (passed + ok, failed + (not ok))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
