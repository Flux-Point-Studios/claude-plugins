#!/usr/bin/env python3
"""Compile a WORK.md declarative IR block into a Claude Code Workflow script.

The graph-ir block in WORK.md is the single source of truth. This compiler
is deterministic and has
no model in the loop, which is the point: the spec cannot drift from the
script, because the script is generated from the spec.

Usage:
  compile-graph.py WORK.md -o .claude/workflows/campaign.graph.js
  compile-graph.py WORK.md --check       # validate only, emit nothing

Exit 0 = green. Any invariant violation exits 1 with the findings on stderr.
Standard library only; no third-party dependencies.
"""
import argparse
import json
import math
import os
import re
import sys

IR_FENCE = re.compile(r"```json\s+graph-ir\s*\n(.*?)\n```", re.S)
# 'harness' is deliberately absent: it was accepted, priced into the budget,
# and emitted nothing. Verifying that something is green is what
# mutates + independent already does, properly. See validate().
TIER = re.compile(r"^(schema-only|skeptic:(\d+)|panel:(\d+))$")
HALT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=|>=|<=|>|<)\s*(-?\d+|'[^']*')\s*$")
SUBST = re.compile(r"\{\{\s*([A-Za-z0-9_.\[\]]+)\s*\}\}")
IDENT = re.compile(r"^[a-z][a-z0-9-]*$")

# Every key the IR may carry, by level. An unknown key is a compile error
# rather than a no-op, because the failure this closes is a field that looks
# honored and is silently inert — a misspelled `verifyOver` used to disable
# verification while the spec still claimed it. tests/emission-test.py holds
# these registries to the second half of the promise: every field listed here
# must demonstrably change what the compiler produces.
IR_FIELDS = {
    "version", "name", "campaign", "budget", "defaults", "roles", "lists",
    "nodes", "requiredArgs", "argDefaults",
}
NODE_FIELDS = {
    "id", "phase", "prompt", "contract", "role", "effort", "model", "agentType",
    "foreach", "after", "mutates", "independent", "verifies", "verify",
    "verifyOver", "expectItems", "haltWhen", "haltReason", "onRed", "isolation",
    "repeat",
}
REPEAT_FIELDS = {"untilDryRounds", "maxRounds", "dedupeBy"}
BUDGET_FIELDS = {"maxNodes", "verifyFloorTokens", "nodeFloorTokens"}
ROLE_FIELDS = {"agentType", "effort", "model"}
DEFAULTS_FIELDS = {"effort", "model"}


class GraphError(Exception):
    pass


def _unknown(where, obj, allowed):
    """Findings for keys that are not part of the IR at this level."""
    if not isinstance(obj, dict):
        return []
    return [
        f"{where}: unknown field '{k}' — not part of the IR, so it would be "
        f"silently ignored (known: {', '.join(sorted(allowed))})"
        for k in sorted(set(obj) - allowed)
    ]


# ---------------------------------------------------------------- extraction
def extract_ir(md_text):
    blocks = IR_FENCE.findall(md_text)
    if not blocks:
        raise GraphError(
            "no IR block found — the work file needs one ```json graph-ir fenced block"
        )
    if len(blocks) > 1:
        raise GraphError(f"{len(blocks)} IR blocks found; exactly one is allowed")
    try:
        return json.loads(blocks[0])
    except json.JSONDecodeError as e:
        raise GraphError(f"IR block is not valid JSON: {e}")


def load_contracts(contracts_dir):
    out = {}
    if not os.path.isdir(contracts_dir):
        return out
    for fn in sorted(os.listdir(contracts_dir)):
        if fn.endswith(".schema.json"):
            with open(os.path.join(contracts_dir, fn)) as fh:
                schema = json.load(fh)
            out[schema.get("$id", fn.split(".")[0])] = schema
    return out


# ---------------------------------------------------------------- validation
def validate(ir, contracts):
    """Return a list of findings. Empty list means the graph may compile."""
    f = []
    if ir.get("version") != 1:
        f.append("IR version must be 1")
    if not ir.get("campaign"):
        f.append("campaign: required, one line naming the goal")

    nodes = ir.get("nodes") or []
    if not nodes:
        f.append("nodes: at least one node required")
    lists = ir.get("lists") or {}
    roles = ir.get("roles") or {}
    seen = {}

    # A lists key is emitted as a JS identifier, so an unconstrained key is
    # arbitrary code in the generated script. Constrain it like a node id.
    for lname in lists:
        if not IDENT.match(str(lname)):
            f.append(
                f"lists: key '{lname}' must be lowercase kebab-case — it is "
                f"emitted as a JS identifier, so anything else is injected code"
            )

    # Strict keys at every level: a typo must fail loudly, never quietly
    # disable the thing it was meant to configure.
    f += _unknown("IR", ir, IR_FIELDS)
    f += _unknown("budget", ir.get("budget"), BUDGET_FIELDS)
    f += _unknown("defaults", ir.get("defaults"), DEFAULTS_FIELDS)
    for rname, rbody in (roles or {}).items():
        f += _unknown(f"role '{rname}'", rbody, ROLE_FIELDS)

    for i, n in enumerate(nodes):
        nid = n.get("id", f"<node {i}>")
        where = f"node '{nid}'"
        f += _unknown(where, n, NODE_FIELDS)
        f += _unknown(f"{where} repeat", n.get("repeat"), REPEAT_FIELDS)
        if not IDENT.match(str(n.get("id", ""))):
            f.append(f"{where}: id must be lowercase kebab-case")
        if nid in seen:
            f.append(f"{where}: duplicate id")
        if not n.get("prompt"):
            f.append(f"{where}: prompt required")

        # Contract layer: a node without a contract does not run.
        c = n.get("contract")
        if not c:
            f.append(f"{where}: contract required — a node without a contract cannot be verified")
        elif c not in contracts:
            f.append(f"{where}: unknown contract '{c}' (have: {', '.join(sorted(contracts)) or 'none'})")

        # Verification tier.
        tier = n.get("verify", "schema-only")
        m = TIER.match(str(tier))
        if str(tier) == "harness":
            f.append(
                f"{where}: verify 'harness' was removed — it compiled to nothing "
                f"while the spec claimed the node was checked. To gate on the "
                f"harness, mark the producing node mutates:true and add a node "
                f"with independent:true, verifies:'{nid}', and a haltWhen on its "
                f"real exit code"
            )
        elif not m:
            f.append(f"{where}: verify must be schema-only | skeptic:N | panel:N")
        else:
            count = m.group(2) or m.group(3)
            if count:
                cnt = int(count)
                if cnt < 1:
                    f.append(f"{where}: verify panel size must be >= 1")
                if m.group(3) and cnt % 2 == 0:
                    f.append(f"{where}: panel:{cnt} is even — majority is undefined; use an odd panel")
                if cnt > 0 and not n.get("verifyOver"):
                    f.append(f"{where}: verify {tier} needs verifyOver naming the array field to verify")

        if n.get("verifyOver") and c in contracts:
            props = contracts[c].get("properties", {})
            if n["verifyOver"] not in props:
                f.append(f"{where}: verifyOver '{n['verifyOver']}' is not a field of {c}")

        # Edge layer: dependencies must already exist (keeps the DAG acyclic
        # and the emission order honest).
        after = n.get("after")
        if after and after not in seen:
            f.append(f"{where}: after '{after}' is not a node defined earlier")
        if after and "{{prev}}" not in str(n.get("prompt", "")):
            # Nodes already run in declaration order, so an `after` whose
            # output is never consumed is a phantom edge: it reads as a
            # dependency in the spec and constrains nothing in the run.
            f.append(
                f"{where}: after '{after}' but the prompt never uses {{{{prev}}}} — "
                f"declaration order already sequences nodes, so `after` means "
                f"'consumes that node's contract'. Use {{{{prev}}}} or drop the field"
            )
        if n.get("foreach") and n["foreach"] not in lists:
            f.append(f"{where}: foreach '{n['foreach']}' has no entry under lists")
        if n.get("role") and n["role"] not in roles:
            f.append(f"{where}: role '{n['role']}' is not declared under roles")

        if n.get("haltWhen"):
            if not HALT.match(str(n["haltWhen"])):
                f.append(f"{where}: haltWhen must be '<field> <op> <literal>' (e.g. 'exit != 0')")
            elif panel_size(n):
                # After a panel the node's value is verified items, not its own
                # contract, so the named field is not there to test. Accepting
                # this would emit a halt that can never fire.
                f.append(
                    f"{where}: haltWhen cannot be combined with verify {n.get('verify')} — "
                    f"the node's value after verification is the surviving items, not "
                    f"its contract, so '{n['haltWhen']}' would never fire. Halt on a "
                    f"separate unverified node, or drop the tier"
                )
            elif HALT.match(str(n["haltWhen"])) and c in contracts:
                field = HALT.match(str(n["haltWhen"])).group(1)
                if field not in contracts[c].get("properties", {}):
                    f.append(
                        f"{where}: haltWhen tests '{field}', which is not a field of "
                        f"{c} — the condition could never fire"
                    )

        # {{prev}} is only meaningful with a declared predecessor; otherwise
        # the node is reading state no edge delivers to it.
        if "{{prev}}" in str(n.get("prompt", "")) and not n.get("after"):
            f.append(f"{where}: prompt uses {{{{prev}}}} but declares no 'after' — hidden coupling")

        # Substitution tokens must resolve to something actually in scope for
        # this node, or the graph compiles clean and dies at launch on a
        # ReferenceError.
        bound = {"A", "campaign"}
        if n.get("after"):
            bound.add("prev")
        if n.get("foreach"):
            bound.update({"item", "i"})
        if n.get("repeat"):
            bound.add("seen")
        for tok in SUBST.findall(str(n.get("prompt", ""))):
            root = tok.split(".")[0].split("[")[0]
            if root not in bound:
                f.append(
                    f"{where}: prompt uses {{{{{tok}}}}} but '{root}' is not in "
                    f"scope for this node (available: {', '.join(sorted(bound))}) — "
                    f"it would compile clean and throw at launch"
                )

        # Discovery loops: unknown-size work needs a dry rule, a hard round
        # ceiling, and a dedup key, or it either never converges or never ends.
        rep = n.get("repeat")
        if "{{seen}}" in str(n.get("prompt", "")) and not rep:
            f.append(f"{where}: prompt uses {{{{seen}}}} but declares no 'repeat' block")
        if rep is not None:
            if not isinstance(rep, dict):
                f.append(f"{where}: repeat must be an object")
            else:
                dry = rep.get("untilDryRounds")
                mx = rep.get("maxRounds")
                if not isinstance(dry, int) or dry < 1:
                    f.append(f"{where}: repeat.untilDryRounds must be an integer >= 1")
                if not isinstance(mx, int) or mx < 1:
                    f.append(f"{where}: repeat.maxRounds must be an integer >= 1 — an unbounded discovery loop has no halt condition")
                if isinstance(dry, int) and isinstance(mx, int) and dry > mx:
                    f.append(f"{where}: repeat.untilDryRounds ({dry}) exceeds maxRounds ({mx}); the dry rule can never fire")
                keys = rep.get("dedupeBy")
                if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
                    f.append(f"{where}: repeat.dedupeBy must be a non-empty list of field names — without a key the loop re-finds the same items forever")
                elif not n.get("verifyOver"):
                    f.append(f"{where}: repeat needs verifyOver naming the array field being discovered")
                elif c in contracts:
                    item_props = (
                        contracts[c].get("properties", {})
                        .get(n["verifyOver"], {})
                        .get("items", {})
                        .get("properties", {})
                    )
                    for k in keys:
                        if item_props and k not in item_props:
                            f.append(f"{where}: repeat.dedupeBy '{k}' is not a field of {c}.{n['verifyOver']} items")

        # Self-report invariant. A node that changes the tree cannot be the
        # node that certifies the change; some later independent node must
        # verify it. This is the feature.graph.js bug, promoted to a rule.
        if n.get("mutates"):
            verifiers = [
                o for o in nodes
                if o.get("independent") and o.get("verifies") == nid
            ]
            if not verifiers:
                f.append(
                    f"{where}: mutates the tree but no node with independent:true "
                    f"verifies:'{nid}' — a mutator may not certify its own work"
                )
        if n.get("verifies"):
            if n["verifies"] not in seen and n["verifies"] != nid:
                f.append(f"{where}: verifies '{n['verifies']}' is not a node defined earlier")
            if n["verifies"] == nid:
                f.append(f"{where}: cannot verify itself")
            if not n.get("independent"):
                f.append(f"{where}: verifies '{n['verifies']}' but is not marked independent:true")
        seen[nid] = n

    # Budget layer: fan-out must fit the declared ceiling.
    budget = ir.get("budget") or {}
    max_nodes = budget.get("maxNodes")
    planned = plan_node_count(ir)
    if max_nodes is not None and planned > max_nodes:
        f.append(
            f"budget: graph plans {planned} agent calls but budget.maxNodes is "
            f"{max_nodes} — raise the ceiling or shrink the fan-out"
        )
    if max_nodes is None:
        f.append("budget.maxNodes: required — an unbounded graph has no halt condition")
    return f


def warnings(ir):
    """Non-blocking findings: shapes that compile but will predictably
    disappoint. Returned separately from validate() so they inform without
    refusing to build."""
    w = []
    for n in ir.get("nodes") or []:
        nid = n.get("id", "?")
        rep = n.get("repeat") or {}
        dry, mx = rep.get("untilDryRounds"), rep.get("maxRounds")
        if isinstance(dry, int) and isinstance(mx, int):
            # A sweep needs room for productive rounds AND the dry streak that
            # proves it is finished. Without that headroom the ceiling, not the
            # dry rule, ends every run — and every run reports INCOMPLETE.
            headroom = mx - dry
            if headroom < 2:
                w.append(
                    f"node '{nid}': maxRounds {mx} leaves only {headroom} round(s) "
                    f"above untilDryRounds {dry}, so the ceiling will end the sweep "
                    f"before the dry rule can — expect INCOMPLETE. Prefer "
                    f"maxRounds >= {dry + 3} unless a short sweep is the point."
                )
        if n.get("repeat") and not panel_size(n) and n.get("verify", "schema-only") == "schema-only":
            w.append(
                f"node '{nid}': discovery with no verification tier — a sweep's "
                f"output is usually consumed as fact; consider skeptic:1 or panel:3"
            )
    return w


def plan_node_count(ir):
    """Worst-case agent calls: fan-out times verification times rounds."""
    lists = ir.get("lists") or {}
    total = 0
    for n in ir.get("nodes") or []:
        fan = len(lists.get(n.get("foreach"), [None])) if n.get("foreach") else 1
        per_round = fan
        m = TIER.match(str(n.get("verify", "schema-only")))
        if m:
            cnt = int(m.group(2) or m.group(3) or 0)
            if cnt:
                # Verification runs per produced item; assume the declared
                # expectation, defaulting to 3 items per producing node.
                per = int(n.get("expectItems", 3))
                per_round += fan * per * cnt
            elif m.group(1) == "harness":
                per_round += fan
        # A discovery node re-runs until it goes dry; the ceiling must price
        # the worst case, not one round of it.
        rounds = int((n.get("repeat") or {}).get("maxRounds", 1))
        total += per_round * max(1, rounds)
    return total


# ------------------------------------------------------------------ emission
def list_var(name):
    """JS identifier for a lists key. Validated by IDENT, so this is a
    rename, not sanitisation — the guarantee lives in validate()."""
    return "LIST_" + name.replace("-", "_")


def panel_size(n):
    """Refuters per item for this node's tier; 0 when it needs none."""
    m = TIER.match(str(n.get("verify", "schema-only")))
    return int(m.group(2) or m.group(3) or 0) if m else 0


def js_str(s):
    return json.dumps(str(s))


def js_template(s, mapping=None):
    """Render an IR prompt as a JS template literal, honoring {{expr}}.

    mapping rewrites bare tokens to JS expressions, which is how {{prev}}
    reaches a downstream node's prompt without a global temp binding.
    """
    mapping = mapping or {}
    s = s.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    s = SUBST.sub(lambda m: "${" + mapping.get(m.group(1), m.group(1)) + "}", s)
    return "`" + s + "`"


def opts(n, ir, phase, label):
    roles = ir.get("roles") or {}
    role = roles.get(n.get("role"), {})
    parts = [f"label: {label}", f"phase: {js_str(phase)}", f"schema: C[{js_str(n['contract'])}]"]
    agent_type = n.get("agentType") or role.get("agentType")
    if agent_type:
        parts.append(f"agentType: {js_str(agent_type)}")
    effort = n.get("effort") or role.get("effort") or (ir.get("defaults") or {}).get("effort")
    if effort:
        parts.append(f"effort: {js_str(effort)}")
    model = n.get("model") or role.get("model")
    if model:
        parts.append(f"model: {js_str(model)}")
    if n.get("isolation") or n.get("mutates"):
        parts.append("isolation: 'worktree'")
    return "{ " + ", ".join(parts) + " }"


def halt_parts(n):
    """(field, js_operator, js_literal) for a validated haltWhen.

    The literal is re-emitted from its parsed value rather than pasted from
    the regex match: a crafted quote in the source text must not be able to
    terminate the string it lands in.
    """
    m = HALT.match(str(n["haltWhen"]))
    field, op, lit = m.group(1), m.group(2), m.group(3)
    op = {"==": "===", "!=": "!=="}.get(op, op)
    lit = json.dumps(int(lit)) if re.fullmatch(r"-?\d+", lit) else json.dumps(lit[1:-1])
    return field, op, lit


def emit_halt(n, var):
    field, op, lit = halt_parts(n)
    return (
        f"if ({var} && {var}.{field} {op} {lit}) {{\n"
        f"  log(`HALT at {n['id']}: {field}=${{{var}.{field}}} — ` + {js_str(n.get('haltReason', 'halt condition met'))})\n"
        f"  RESULTS[{js_str(n['id'])}] = {var}\n"
        f"  return summary('HALTED')\n"
        f"}}"
    )


def emit_halt_any(n, var):
    """haltWhen across a fan-out: any tripping item halts the campaign."""
    field, op, lit = halt_parts(n)
    return (
        f"const tripped_{var} = {var}.filter(r => r && r.{field} {op} {lit})\n"
        f"if (tripped_{var}.length) {{\n"
        f"  log(`HALT at {n['id']}: ${{tripped_{var}.length}}/${{{var}.length}} item(s) "
        f"tripped {field} {op} {lit} — ` + {js_str(n.get('haltReason', 'halt condition met'))})\n"
        f"  note({js_str(n['id'])}, 'HALTED', `${{tripped_{var}.length}} item(s) tripped "
        f"{field}`)\n"
        f"  RESULTS[{js_str(n['id'])}] = {var}\n"
        f"  return summary('HALTED')\n"
        f"}}"
    )


def emit(ir, contracts):
    nodes = ir["nodes"]
    lists = ir.get("lists") or {}
    used = sorted({n["contract"] for n in nodes})
    phases, seen_phase = [], set()
    for n in nodes:
        p = n.get("phase", "Run")
        if p not in seen_phase:
            seen_phase.add(p)
            phases.append(p)
    needs_panel = any(panel_size(n) for n in nodes)

    L = []
    a = L.append
    a("// GENERATED by fluxpoint compile-graph.py — DO NOT EDIT.")
    a("// Source of truth is the ```json graph-ir block in WORK.md.")
    a("// Regenerate with /fluxpoint:graph-run (or compile-graph.py).")
    a("export const meta = {")
    a(f"  name: {js_str(ir.get('name') or 'graph-campaign')},")
    a(f"  description: {js_str(ir['campaign'])},")
    a("  phases: [")
    for p in phases:
        a(f"    {{ title: {js_str(p)} }},")
    a("  ],")
    a("}")
    a("")
    a("// --- contracts (from contracts/*.schema.json, referenced by name) ---")
    a("const C = {")
    for name in used:
        a(f"  {json.dumps(name)}: {json.dumps(contracts[name], indent=2)},")
    a("}")
    a("")
    a("// --- inputs: normalize object | JSON string | bare string, fail loudly ---")
    a("const A =")
    a("  args && typeof args === 'object'")
    a("    ? args")
    a("    : typeof args === 'string' && args.trim()")
    a("    ? (() => { try { return JSON.parse(args) } catch { return { target: args } } })()")
    a("    : {}")
    for req in ir.get("requiredArgs") or []:
        a(f"if (!A[{js_str(req)}]) throw new Error({js_str('missing required arg: ' + req)})")
    for k, v in (ir.get("argDefaults") or {}).items():
        a(f"if (!A[{js_str(k)}]) A[{js_str(k)}] = {js_str(v)}")
    a(f"const campaign = {js_str(ir['campaign'])}")
    a("// Resolved inputs are logged, never silently defaulted behind your back.")
    a("log(`inputs: ${JSON.stringify(A)}`)")
    a("")
    if lists:
        a("// --- lists ---")
        for name, items in lists.items():
            a(f"const {list_var(name)} = {json.dumps(items, indent=2)}")
        a("")
    a("const RESULTS = {}")
    a("const PROVENANCE = []")
    a("function note(id, status, detail) { PROVENANCE.push({ node: id, status, detail: detail || '' }) }")
    # Which node returned which contract is known here and nowhere else.
    # Without it a reader of the summary has to guess a result's type from
    # its shape, and a recorder that guesses will eventually file a harness
    # exit code as a red-team verdict.
    a("// nodeId -> contract, so a consumer of the summary reads types rather")
    a("// than sniffing them out of the result's shape.")
    a("const CONTRACTS = {")
    for n in ir["nodes"]:
        a(f"  {js_str(n['id'])}: {js_str(n['contract'])},")
    a("}")
    a("// Set whenever the campaign covered less ground than it set out to —")
    a("// budget declined work, or a sweep ended on its ceiling with more to")
    a("// find. It rides all the way out to the Evidence row, so a partial run")
    a("// can never be read as a clean one.")
    a("let INCOMPLETE = false")
    a("function summary(outcome) {")
    a("  const final = outcome === 'COMPLETE' && INCOMPLETE ? 'INCOMPLETE' : outcome")
    a("  return { campaign, outcome: final, results: RESULTS, provenance: PROVENANCE,")
    a("           contracts: CONTRACTS }")
    a("}")
    a("")
    budget_cfg = ir.get("budget") or {}
    floor = budget_cfg.get("verifyFloorTokens", 50000)
    node_floor = budget_cfg.get("nodeFloorTokens", floor)
    a("// --- budget: work nodes get their own floor, not just verification ---")
    a(f"const NODE_FLOOR = {int(node_floor)}")
    max_nodes = budget_cfg.get("maxNodes")
    if max_nodes is not None:
        a("// maxNodes was a compile-time estimate only, and the estimate leans on")
        a("// expectItems, which is a guess. A run that found more than expected")
        a("// could quietly exceed its own declared ceiling, so the ceiling is")
        a("// counted at run time too.")
        a(f"const MAX_NODES = {int(max_nodes)}")
        a("let SPAWNED = 0")
        a("// Every agent in this graph is spawned through here, so the ceiling")
        a("// counts what actually ran. Declining returns null, which every call")
        a("// site already treats as a dead node.")
        a("async function spawn(prompt, opts) {")
        a("  if (SPAWNED >= MAX_NODES) {")
        a("    log(`budget ceiling: ${opts.label} NOT RUN — ${SPAWNED}/${MAX_NODES} agent call(s) already spawned`)")
        a("    INCOMPLETE = true")
        a("    return null")
        a("  }")
        a("  SPAWNED++")
        a("  return agent(prompt, opts)")
        a("}")
    a("// True when there is room to spawn work. Anything declined is announced")
    a("// and recorded as SKIPPED — a graph never quietly does less than it says.")
    a("function affordable(label) {")
    a("  if (!budget.total) return true")
    a("  const rem = budget.remaining()")
    a("  if (rem < NODE_FLOOR) {")
    a("    log(`budget floor: ${label} NOT RUN — ${Math.round(rem / 1000)}k remaining < ${Math.round(NODE_FLOOR / 1000)}k floor`)")
    a("    INCOMPLETE = true")
    a("    return false")
    a("  }")
    a("  return true")
    a("}")
    a("")
    if needs_panel:
        a("// --- verification: refuters attack the claim; majority kills it ---")
        a(f"const VERIFY_FLOOR = {int(floor)}")
        a("async function refute(claimText, label, phase, n) {")
        a("  if (budget.total && budget.remaining() < VERIFY_FLOOR) {")
        a("    log(`budget floor reached — ${label} left UNVERIFIED (no silent caps)`)")
        a("    return { kills: 0, cast: 0, unverified: true }")
        a("  }")
        a("  const votes = await parallel(Array.from({ length: n }, (_, i) => () =>")
        a("    spawn(")
        a("      `Attempt to REFUTE this claim. ${claimText}\\n\\n` +")
        a("        `Re-read the underlying code or evidence YOURSELF; do not trust the claim's own summary. ` +")
        a("        `Hunt for the reason it is wrong: a guard upstream, a type that forbids the state, a test that pins it. ` +")
        a("        `Default to refuted=true when uncertain.`,")
        a("      { label: `${label}:refute${i + 1}`, phase, schema: C.VerdictV1, effort: 'low' }")
        a("    )")
        a("  ))")
        a("  const cast = votes.filter(Boolean)")
        a("  // Any missing vote — a dead refuter or one declined by the node")
        a("  // ceiling — means this claim was not fully checked, and says so.")
        a("  if (cast.length < n) log(`${label}: only ${cast.length}/${n} votes cast — UNVERIFIED`)")
        a("  return {")
        a("    kills: cast.filter(v => v.refuted).length,")
        a("    cast: cast.length,")
        a("    unverified: cast.length < n,")
        a("  }")
        a("}")
        a("")
        a("// Applies a node's declared tier to every item it produced. Used by")
        a("// fan-out and single nodes alike, so a declared tier always runs.")
        a("async function verifyItems(result, field, label, phase, n, need) {")
        a("  if (!result) return []")
        a("  const produced = result[field] || []")
        a("  const judged = await parallel(produced.map((it, i) => () =>")
        a("    refute(JSON.stringify(it), `${label}:${i}`, phase, n).then(v => ({ ...it, ...v }))")
        a("  ))")
        a("  return judged.filter(Boolean).filter(v => v.kills < need)")
        a("}")
        a("")

    for n in nodes:
        a(emit_node(n, ir))
        a("")
    a("return summary('COMPLETE')")
    return "\n".join(L) + "\n"


def emit_node(n, ir):
    nid = n["id"]
    var = "n_" + nid.replace("-", "_")
    phase = n.get("phase", "Run")
    tier = str(n.get("verify", "schema-only"))
    m = TIER.match(tier)
    panel = int(m.group(2) or m.group(3) or 0) if m else 0
    is_panel = m and m.group(3)
    over = n.get("verifyOver")
    # {{prev}} carries the predecessor's contract into this prompt — the
    # justified barrier (judging candidates side by side, reducing a set).
    mapping = {}
    if n.get("after"):
        mapping["prev"] = f"JSON.stringify(RESULTS[{js_str(n['after'])}])"
    if n.get("repeat"):
        # Later rounds are told what earlier rounds already surfaced, so the
        # finder spends its round on new ground instead of re-reporting.
        mapping["seen"] = f"(seenList_{var}.join('; ') || 'nothing yet')"
    prompt = js_template(n["prompt"], mapping)
    L = []
    a = L.append
    a(f"// ===== node {nid} ({tier}{', mutates' if n.get('mutates') else ''}"
      f"{', independent' if n.get('independent') else ''}) =====")
    a(f"phase({js_str(phase)})")

    if n.get("repeat"):
        a(emit_repeat(n, ir, prompt, phase, panel, over))
    elif n.get("foreach"):
        lst = list_var(n["foreach"])
        label = f"`{nid}:${{item.key || i}}`"
        a(f"let {var} = []")
        a(f"if (!affordable({js_str('node ' + nid)})) {{")
        a(f"  note({js_str(nid)}, 'SKIPPED', 'budget floor reached before fan-out')")
        a("} else {")
        if panel and over:
            a(f"  {var} = (await pipeline(")
            a(f"    {lst},")
            a(f"    (item, _o, i) => spawn({prompt}, {opts(n, ir, phase, label)}),")
            a("    async (prev, item, i) => {")
            a(f"      if (!prev) {{ note({js_str(nid)}, 'DEAD', `${{item.key || i}} produced nothing`); "
              f"log(`node {nid} died for ${{item.key || i}} — dropped`); return [] }}")
            a(f"      const produced = prev[{js_str(over)}] || []")
            a(f"      note({js_str(nid)}, 'OK', `${{item.key || i}}: ${{produced.length}} item(s)`)")
            need = math.ceil(panel / 2)
            a(f"      const judged = await verifyItems(prev, {js_str(over)}, "
              f"`{nid}:${{item.key || i}}`, {js_str(phase)}, {panel}, {need})")
            a("      return judged.map(v => ({ ...v, source: item.key || String(i) }))")
            a("    }")
            a("  )).filter(Boolean).flat()")
            a(f"  log(`{nid}: ${{{var}.length}} item(s) survived {tier}`)")
        else:
            a(f"  {var} = (await parallel({lst}.map((item, i) => () =>")
            a(f"    spawn({prompt}, {opts(n, ir, phase, label)})")
            a("  ))).filter(Boolean)")
            a(f"  note({js_str(nid)}, {var}.length ? 'OK' : 'DEAD', `${{{var}.length}}/${{{lst}.length}} returned`)")
            a(f"  if ({var}.length < {lst}.length) log(`{nid}: "
              f"${{{lst}.length - {var}.length}} node(s) died — see provenance`)")
        a("}")
        if n.get("haltWhen"):
            # One item tripping the condition halts the campaign: a fan-out
            # gate that only fired when every branch failed would not be a gate.
            a(emit_halt_any(n, var))
    else:
        label = f"{js_str(nid)}"
        verified = bool(panel and over)
        raw = f"{var}_raw" if verified else var
        on_red = n.get("onRed", "halt")
        a(f"if (!affordable({js_str('node ' + nid)})) {{")
        a(f"  note({js_str(nid)}, 'SKIPPED', 'budget floor reached')")
        if on_red == "halt":
            a("  return summary('BUDGET-EXHAUSTED')")
        a("}")
        a(f"const {raw} = affordable({js_str('node ' + nid)}) ? await spawn({prompt}, {opts(n, ir, phase, label)}) : null")
        a(f"if (!{raw}) {{")
        a(f"  note({js_str(nid)}, 'DEAD', 'node returned nothing')")
        if on_red == "halt":
            a(f"  log(`node {nid} died — halting; an unverified gate never passes by default`)")
            a("  return summary('NODE-DEAD')")
        else:
            a(f"  log(`node {nid} died — continuing per onRed={on_red}`)")
        a("} else {")
        a(f"  note({js_str(nid)}, 'OK', '')")
        a("}")
        if n.get("haltWhen"):
            # Halt on the raw contract: the gate reads the node's own fields.
            a(emit_halt(n, raw))
        if verified:
            # A declared tier always runs, fan-out or not.
            need = math.ceil(panel / 2)
            a(f"const {var} = await verifyItems({raw}, {js_str(over)}, "
              f"{js_str(nid)}, {js_str(phase)}, {panel}, {need})")
            a(f"log(`{nid}: ${{{var}.length}} item(s) survived {tier}`)")

    a(f"RESULTS[{js_str(nid)}] = {var}")
    return "\n".join(L)


def emit_repeat(n, ir, prompt, phase, panel, over):
    """Loop-until-dry discovery: re-run the finder until K consecutive rounds
    surface nothing new, bounded by maxRounds and the budget floor.

    The dedup set holds everything SEEN, not everything confirmed — dedup
    against survivors instead and every judge-rejected item reappears next
    round, so the loop never converges.
    """
    nid = n["id"]
    var = "n_" + nid.replace("-", "_")
    rep = n["repeat"]
    keys = rep["dedupeBy"]
    dry_target = int(rep["untilDryRounds"])
    max_rounds = int(rep["maxRounds"])
    need = math.ceil(panel / 2) if panel else 0
    lst = list_var(n["foreach"]) if n.get("foreach") else None
    label = f"`{nid}:${{item.key || i}}`" if lst else js_str(nid)

    L = []
    a = L.append
    a(f"const {var} = []")
    a(f"const seen_{var} = new Set()")
    a(f"const seenList_{var} = []")
    a(f"let dry_{var} = 0, round_{var} = 0")
    keyexpr = " + '|' + ".join(f"String(it[{js_str(k)}])" for k in keys)
    a(f"const key_{var} = it => {keyexpr}")
    a(f"while (dry_{var} < {dry_target} && round_{var} < {max_rounds}) {{")
    a(f"  round_{var}++")
    a(f"  if (!affordable(`{nid} round ${{round_{var}}}`)) {{")
    a(f"    note({js_str(nid)}, 'SKIPPED', `stopped at round ${{round_{var}}} on budget floor; "
      f"discovery INCOMPLETE`)")
    a("    break")
    a("  }")
    # Round 1 of the fan-out, unverified — dedup happens before verification
    # so the panel never re-judges an item a previous round already saw.
    if lst:
        a(f"  const raw_{var} = (await parallel({lst}.map((item, i) => () =>")
        a(f"    spawn({prompt}, {opts(n, ir, phase, label)})")
        a("  ))).filter(Boolean)")
    else:
        a(f"  const one_{var} = await spawn({prompt}, {opts(n, ir, phase, label)})")
        a(f"  const raw_{var} = one_{var} ? [one_{var}] : []")
    a(f"  const found_{var} = raw_{var}.flatMap(r => r[{js_str(over)}] || [])")
    a(f"  const fresh_{var} = found_{var}.filter(it => !seen_{var}.has(key_{var}(it)))")
    a(f"  fresh_{var}.forEach(it => {{ seen_{var}.add(key_{var}(it)); "
      f"seenList_{var}.push(key_{var}(it)) }})")
    a(f"  log(`{nid} round ${{round_{var}}}: ${{found_{var}.length}} found, "
      f"${{fresh_{var}.length}} new`)")
    a(f"  if (!fresh_{var}.length) {{")
    a(f"    dry_{var}++")
    a(f"    note({js_str(nid)}, 'OK', `round ${{round_{var}}} dry "
      f"(${{dry_{var}}}/{dry_target})`)")
    a("    continue")
    a("  }")
    a(f"  dry_{var} = 0")
    if panel and over:
        a(f"  const judged_{var} = await parallel(fresh_{var}.map((it, i) => () =>")
        a(f"    refute(JSON.stringify(it), `{nid}:r${{round_{var}}}:${{i}}`, "
          f"{js_str(phase)}, {panel}).then(v => ({{ ...it, ...v }}))")
        a("  ))")
        a(f"  const kept_{var} = judged_{var}.filter(Boolean)"
          f".filter(v => v.kills < {need})")
    else:
        a(f"  const kept_{var} = fresh_{var}")
    a(f"  {var}.push(...kept_{var})")
    a(f"  note({js_str(nid)}, 'OK', `round ${{round_{var}}}: ${{kept_{var}.length}} kept "
      f"of ${{fresh_{var}.length}} new`)")
    a("}")
    a(f"if (round_{var} >= {max_rounds} && dry_{var} < {dry_target}) {{")
    a(f"  log(`{nid}: hit maxRounds {max_rounds} while still finding new items — "
      f"discovery INCOMPLETE, not exhausted`)")
    a(f"  note({js_str(nid)}, 'INCOMPLETE', `ended on the {max_rounds}-round ceiling "
      f"with new items still arriving; the sweep is not exhaustive`)")
    a("  INCOMPLETE = true")
    a("}")
    a(f"log(`{nid}: ${{{var}.length}} item(s) kept across ${{round_{var}}} round(s)`)")
    return "\n".join(L)


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("graph", nargs="?", default="WORK.md", help="path to WORK.md (or any file with a graph-ir block)")
    ap.add_argument("-o", "--out", help="path to write the compiled .graph.js")
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--contracts", help="contracts directory")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    contracts_dir = args.contracts or os.path.join(os.path.dirname(here), "contracts")

    try:
        with open(args.graph) as fh:
            ir = extract_ir(fh.read())
    except (OSError, GraphError) as e:
        print(f"graph-compile: {e}", file=sys.stderr)
        return 1

    contracts = load_contracts(contracts_dir)
    if not contracts:
        print(f"graph-compile: no contracts found in {contracts_dir}", file=sys.stderr)
        return 1

    findings = validate(ir, contracts)
    if findings:
        print("graph-compile: IR rejected\n", file=sys.stderr)
        for f in findings:
            print(f"  - {f}", file=sys.stderr)
        return 1

    planned = plan_node_count(ir)
    # Warnings inform, never block: these shapes are legal and occasionally
    # deliberate, but they will usually disappoint whoever reads the result.
    for w in warnings(ir):
        print(f"graph-compile: warning — {w}", file=sys.stderr)

    if args.check:
        print(f"graph-compile: IR valid — {len(ir['nodes'])} node(s), "
              f"{planned} planned agent call(s), budget.maxNodes="
              f"{(ir.get('budget') or {}).get('maxNodes')}")
        return 0

    js = emit(ir, contracts)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(js)
        print(f"graph-compile: wrote {args.out} — {len(ir['nodes'])} node(s), "
              f"{planned} planned agent call(s)")
    else:
        sys.stdout.write(js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
