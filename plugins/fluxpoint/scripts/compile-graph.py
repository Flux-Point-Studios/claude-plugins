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
# Hyphens are in the class because decision ids are kebab-case by rule, so
# {{decisions.vault-window}} has to be a token the substituter can see. An
# unrecognised root is a compile error either way, so widening this cannot
# turn a literal into a silent expression.
SUBST = re.compile(r"\{\{\s*([A-Za-z0-9_.\-\[\]]+)\s*\}\}")
IDENT = re.compile(r"^[a-z][a-z0-9-]*$")

# Every key the IR may carry, by level. An unknown key is a compile error
# rather than a no-op, because the failure this closes is a field that looks
# honored and is silently inert — a misspelled `verifyOver` used to disable
# verification while the spec still claimed it. tests/emission-test.py holds
# these registries to the second half of the promise: every field listed here
# must demonstrably change what the compiler produces.
IR_FIELDS = {
    "version", "name", "campaign", "budget", "defaults", "roles", "lists",
    "nodes", "requiredArgs", "argDefaults", "imports",
}
NODE_FIELDS = {
    "id", "phase", "prompt", "contract", "role", "effort", "model", "agentType",
    "foreach", "after", "mutates", "independent", "verifies", "verify",
    "verifyOver", "expectItems", "haltWhen", "haltReason", "onRed", "isolation",
    "repeat", "irreversible", "actor", "release", "wake", "decides", "honors",
}
ACTORS = {"agent", "human", "third-party"}
RELEASE_FIELDS = {"instructions", "proofContract", "whyNotAgent"}
WAKE_FIELDS = {"check", "everyMinutes", "deadline"}
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
            with open(os.path.join(contracts_dir, fn), encoding="utf-8") as fh:
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
    decided = {}          # decision id -> the node that makes it
    imports = ir.get("imports") or {}

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
    # A ten-node ceiling forces big campaigns to split, and a split is
    # lossy unless a frozen decision can cross the boundary.
    if imports and not isinstance(imports, dict):
        f.append("imports: must be an object of decisionId -> runId | 'latest'")
    elif imports:
        for k, v in imports.items():
            if not IDENT.match(str(k)):
                f.append(f"imports: key '{k}' must be lowercase kebab-case")
            if not isinstance(v, str) or not v.strip():
                f.append(f"imports.{k}: must be a runId or 'latest'")
    for rname, rbody in (roles or {}).items():
        f += _unknown(f"role '{rname}'", rbody, ROLE_FIELDS)

    for i, n in enumerate(nodes):
        nid = n.get("id", f"<node {i}>")
        where = f"node '{nid}'"
        f += _unknown(where, n, NODE_FIELDS)
        f += _unknown(f"{where} repeat", n.get("repeat"), REPEAT_FIELDS)
        f += _unknown(f"{where} release", n.get("release"), RELEASE_FIELDS)
        f += _unknown(f"{where} wake", n.get("wake"), WAKE_FIELDS)
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

        hon = n.get("honors") or []

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
            if root == "decisions":
                # Scoped per id on purpose: honoring one decision must not
                # hand the node every decision the campaign ever made.
                want = tok.split(".", 1)[1] if "." in tok else ""
                if want not in (hon or []):
                    f.append(
                        f"{where}: prompt uses {{{{{tok}}}}} but '{want}' is not "
                        f"in this node's honors — name it there, so what binds "
                        f"this node is declared rather than implied"
                    )
                continue
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
        # Decisions. DesignV1 has summary/plan/files/risks and none of them
        # is *the choice*, so a frozen architectural decision had to be
        # smuggled into free text and the rejected alternatives had nowhere
        # to go at all. A decision that overturns the prior is precisely the
        # one a fresh context re-decides the other way, and when the
        # parameter freezes at genesis that re-decision is unrecoverable.
        if n.get("decides"):
            if c != "DecisionV1":
                f.append(
                    f"{where}: decides requires contract DecisionV1 (has '{c}') "
                    f"— the record is the point, not the prose around it"
                )
            if not IDENT.match(str(n["decides"])):
                f.append(f"{where}: decides id must be lowercase kebab-case")
            if n["decides"] in decided:
                f.append(f"{where}: decides '{n['decides']}' is already decided by "
                         f"node '{decided[n['decides']]}'")
            else:
                decided[n["decides"]] = nid
        if n.get("honors") is not None:
            if not isinstance(n["honors"], list) or not all(
                    isinstance(h, str) for h in n["honors"]):
                f.append(f"{where}: honors must be a list of decision ids")
            else:
                for h in hon:
                    if h not in decided and h not in imports:
                        f.append(
                            f"{where}: honors '{h}' is neither decided by an "
                            f"earlier node nor listed under imports — a decision "
                            f"this node cannot see cannot bind it"
                        )
                    # Same rule as `after`: a declared dependency the prompt
                    # never reads is a phantom. It looks binding in the spec
                    # and constrains nothing in the run, which is worse than
                    # not declaring it — a reader would believe it held.
                    if ("{{decisions." + h + "}}") not in str(n.get("prompt", "")):
                        f.append(
                            f"{where}: honors '{h}' but the prompt never uses "
                            f"{{{{decisions.{h}}}}} — the decision would not reach "
                            f"the agent, so nothing would be bound by it"
                        )

        # Actors. The engine had two responses to a node it could not
        # complete — halt the campaign, or drop the item and march on with a
        # null — and no third state for "this one is blocked, work the other
        # branches". Every real delivery has nodes only a human or a third
        # party can execute.
        actor = n.get("actor", "agent")
        if actor not in ACTORS:
            f.append(f"{where}: actor must be one of {', '.join(sorted(ACTORS))}")
        if actor != "agent":
            rel = n.get("release")
            if not isinstance(rel, dict):
                f.append(
                    f"{where}: actor '{actor}' needs a release block — a node no "
                    f"agent can run is a dead stop unless it says what unblocks it"
                )
            else:
                if not str(rel.get("instructions", "")).strip():
                    f.append(
                        f"{where}: release.instructions required — this text is "
                        f"the entire message the blocked human gets"
                    )
                # Parking is a last resort, not a first response. Most things
                # that feel human-only are not: a CLI, an API, or a headless
                # browser does them. Naming what was ruled out is the cheapest
                # way to stop a node being parked out of habit.
                if not str(rel.get("whyNotAgent", "")).strip():
                    f.append(
                        f"{where}: release.whyNotAgent required — say what makes "
                        f"this impossible for an agent (key material it must not "
                        f"hold, legal authority, physical possession, another "
                        f"party's action), because a step a CLI or a headless "
                        f"browser could do should not be parked on a person"
                    )
                pc = rel.get("proofContract")
                if not pc:
                    f.append(f"{where}: release.proofContract required")
                elif pc not in contracts:
                    f.append(f"{where}: release.proofContract '{pc}' is not a known contract")
                elif c and pc != c:
                    f.append(
                        f"{where}: release.proofContract '{pc}' differs from the "
                        f"node's contract '{c}' — the node yields exactly what the "
                        f"operator pastes, so downstream would be promised a shape "
                        f"the release can never produce"
                    )
            for bad in ("mutates", "irreversible", "foreach", "repeat", "verify"):
                if n.get(bad) and not (bad == "verify" and n.get(bad) == "schema-only"):
                    f.append(
                        f"{where}: actor '{actor}' cannot be combined with "
                        f"'{bad}' — no agent runs this node, so there is nothing "
                        f"for it to isolate, fan out, or verify"
                    )
        wake = n.get("wake")
        if wake is not None:
            if actor == "agent":
                f.append(
                    f"{where}: wake is only meaningful with actor human or "
                    f"third-party — an agent node is not waiting on anyone"
                )
            if not isinstance(wake, dict):
                f.append(f"{where}: wake must be an object")
            else:
                if not str(wake.get("check", "")).strip():
                    f.append(
                        f"{where}: wake.check required — a poll with no predicate "
                        f"never fires, and the node waits forever"
                    )
                ev = wake.get("everyMinutes")
                if not isinstance(ev, int) or ev < 1:
                    f.append(f"{where}: wake.everyMinutes must be an integer >= 1")

        # Irreversible effects. `mutates` buys worktree isolation, which is
        # real containment for a filesystem write and none at all for a chain
        # write — the same marker covering "edit a test file" and "mint a
        # one-shot NFT" reads as protection it does not provide.
        if n.get("irreversible"):
            if n.get("foreach"):
                f.append(
                    f"{where}: irreversible cannot be combined with foreach — "
                    f"a fan-out of unrepeatable effects under one confirmation "
                    f"authorizes N ceremonies by naming one. Declare each"
                )
            if n.get("repeat"):
                f.append(
                    f"{where}: irreversible cannot be combined with repeat — "
                    f"a discovery loop re-fires its node by design"
                )
            # The adversarial gate has to be ordered BEFORE the effect. A
            # verifier that runs after cannot un-mint an NFT.
            guards = [
                o for o in seen.values()
                if o.get("independent") and o.get("haltWhen") and o.get("verifies")
            ]
            if not guards:
                f.append(
                    f"{where}: irreversible but no earlier node with "
                    f"independent:true, verifies:'<node>' and a haltWhen — the "
                    f"gate must be ordered before the effect, because a verifier "
                    f"that runs afterwards cannot undo it"
                )
            if "confirm" not in (ir.get("requiredArgs") or []):
                f.append(
                    f"{where}: irreversible requires 'confirm' in requiredArgs — "
                    f"the run must refuse to start unless a human named this node "
                    f"in --confirm, not merely launched the campaign"
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
        # A parked node spawns no worker, but it does spawn one advisor to
        # produce the recommendation that goes with the block. Pricing it at
        # zero would make the ceiling lie by exactly the number of parks.
        if n.get("actor", "agent") != "agent":
            total += 1
            continue
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
    used = sorted({n["contract"] for n in nodes}
                  # The advisor emits DecisionV1 whether or not a node
                  # declares it, so its schema has to be in scope.
                  | ({"DecisionV1"} if any(
                      x.get("actor", "agent") != "agent" for x in nodes) else set()))
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
    decides_any = [n for n in nodes if n.get("decides")]
    imports = ir.get("imports") or {}
    if decides_any or imports:
        a("// --- decisions: the choice itself, not the prose around it ---")
        a("// A decision that overturned the prior is the one a fresh context")
        a("// silently re-decides the other way, and for a parameter that")
        a("// freezes at genesis the re-decision is unrecoverable.")
        a("const DECISIONS = {}")
    if imports:
        a("// Imported from earlier campaigns. A ten-node ceiling forces a big")
        a("// campaign to split, and the split is lossy unless a frozen choice")
        a("// can cross the boundary — so an absent one throws at launch")
        a("// rather than letting this run re-decide it by accident.")
        a("const _imported = (A && A._decisions) || {}")
        for k in sorted(imports):
            msg = js_str(
                f"missing imported decision: {k} — load it with "
                f"/fluxpoint:graph-run, which resolves imports from "
                f".claude/fluxpoint/runs")
            a(f"if (!_imported[{js_str(k)}]) throw new Error({msg})")
            a(f"DECISIONS[{js_str(k)}] = _imported[{js_str(k)}]")
        a("")
    parked = [n for n in nodes if n.get("actor", "agent") != "agent"]
    if parked:
        a("// --- actors: nodes no agent can run ---")
        a("// Without this the engine had two answers for a node it could not")
        a("// complete: halt everything, or drop it and continue with a null.")
        a("// Neither is 'this one is blocked, work the other branches'.")
        a("if (!A._releases || typeof A._releases !== 'object')")
        a("  throw new Error('this graph has human or third-party nodes but no "
          "releases map — launch it with /fluxpoint:graph-run, which loads "
          ".claude/fluxpoint/releases into args._releases')")
        a("const RELEASES = A._releases")
        a("// Blocked is inherited: handing a dependent the literal null of a")
        a("// node nobody ran would report a failure where there is a wait.")
        a("const BLOCKED = new Set()")
        a("// A block handed over with no recommendation is a punt. Each")
        a("// one carries the reasoned alternative the advisor produced.")
        a("const RECOMMENDATIONS = {}")
        a("const WAITS = []")
        a("")
    if any(n.get("irreversible") for n in nodes):
        a("// --- irreversible effects: once-only ledger ---")
        a("// Resume is the flagship recovery path and also the operation that")
        a("// double-mints: repairing any upstream node re-fires everything")
        a("// after it. The ledger is consulted before every irreversible")
        a("// spawn, so replay-safety is structural rather than something the")
        a("// operator has to remember. It cannot help across a crash between")
        a("// the effect and the run's end — nothing this script can reach")
        a("// could, since it has no filesystem.")
        a("if (!A._ledger || typeof A._ledger !== 'object')")
        a("  throw new Error('this graph has irreversible nodes but no ledger was "
          "passed — launch it with /fluxpoint:graph-run, which loads "
          ".claude/fluxpoint/irreversible.jsonl into args._ledger')")
        a("const LEDGER = A._ledger")
        a("const LEDGER_WRITES = []")
        a("// FNV-1a over the resolved prompt. A change-detector, not a security")
        a("// boundary: a different prompt is a different operation and earns a")
        a("// new key, which is also why editing a ceremony prompt re-arms it.")
        a("// The confirm gate is what backstops that.")
        a("function ledgerKey(id, prompt) {")
        a("  let h = 2166136261")
        a("  for (let i = 0; i < prompt.length; i++) {")
        a("    h ^= prompt.charCodeAt(i); h = Math.imul(h, 16777619)")
        a("  }")
        a("  return `${campaign}|${id}|${(h >>> 0).toString(16)}`")
        a("}")
        a("// Naming the campaign is not naming the effect: --confirm lists the")
        a("// node ids a human authorized, so a blanket yes cannot carry an")
        a("// unrelated ceremony along with it.")
        a("function confirmed(id) {")
        a("  return String((A && A.confirm) || '').split(',')")
        a("    .map(s => s.trim()).filter(Boolean).includes(id)")
        a("}")
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
    extra = ""
    if any(n.get("irreversible") for n in nodes):
        extra += ", ledger: LEDGER_WRITES"
    if parked:
        extra += ", blocked: [...BLOCKED], waits: WAITS, recommendations: RECOMMENDATIONS"
    if decides_any or imports:
        extra += ", decisions: DECISIONS"
    a("  return { campaign, outcome: final, results: RESULTS, provenance: PROVENANCE,")
    a(f"           contracts: CONTRACTS{extra} }}")
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
        a("    // The arguments that produced the verdict, not just the tally.")
        a("    // Discarding them threw away the most reusable thing a panel")
        a("    // makes: a survivor with its strongest objection recorded is")
        a("    // worth more later than a survivor with a vote count.")
        a("    objections: cast.map(v => v.reason).filter(Boolean).slice(0, 5),")
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


def emit_parked(n):
    """A node no agent can run: released from a file, or reported blocked.

    Emits no spawn at all. The campaign continues past it rather than
    halting, because the branches that do not depend on this node are still
    workable — and it is marked INCOMPLETE, so a run carrying a blocked node
    can never be read as a finished one.
    """
    nid = n["id"]
    var = "n_" + nid.replace("-", "_")
    rel = n.get("release") or {}
    actor = n.get("actor")
    instructions = str(rel.get("instructions", ""))
    L = []
    a = L.append
    a(f"const rel_{var} = RELEASES[{js_str(nid)}] || null")
    a(f"let {var} = null")
    a(f"if (rel_{var}) {{")
    a(f"  {var} = rel_{var}.proof")
    a(f"  note({js_str(nid)}, 'RELEASED', `released by ${{rel_{var}.by || 'operator'}}`)")
    a(f"  log(`{nid}: released — the {actor} step is done`)")
    a("} else {")
    # Handing someone a block with no recommendation is a punt. The advisor
    # runs before the block is reported, is contracted to DecisionV1 so a
    # bare "ask the operator" cannot satisfy it, and is asked first whether
    # the block is real — most steps that feel human-only are not.
    a(f"  const advice_{var} = affordable({js_str('advice for ' + nid)})")
    a(f"    ? await spawn(")
    a(f"        `A campaign step cannot be run by an agent and is about to be "
      f"handed to a person. Do not simply agree.\\n\\n` +")
    a(f"        `The step: ` + {js_str(str(n['prompt']))} + `\\n` +")
    a(f"        `What the human is being asked to do: ` + {js_str(instructions)} + `\\n` +")
    a(f"        `The stated reason no agent can do it: ` + "
      f"{js_str(str(rel.get('whyNotAgent', 'unstated')))} + `\\n\\n` +")
    a("        `FIRST, challenge that reason. Could this actually be done "
      "without a person — a CLI, an API, a headless browser, a read-only "
      "query, a generated file the human only has to sign? If yes, your "
      "recommendation is that concrete agent-executable path, and say what "
      "tooling it needs. Only genuine blockers survive: key material an "
      "agent must not hold, legal authority, physical possession, or "
      "another party's own action.` +")
    a("        `\\n\\nTHEN recommend the single best course of action for the "
      "human, with the alternatives you rejected and the strongest objection "
      "to each — including to the one you are recommending. Ground it in "
      "this repo: read what you need to. A recommendation with no reasoning "
      "is worth less than no recommendation, because it will be followed.`,")
    a(f"        {{ label: {js_str(nid + ':advice')}, phase: {js_str(n.get('phase', 'Run'))}, "
      f"schema: C.DecisionV1, effort: 'medium' }}")
    a("      )")
    a("    : null")
    a(f"  if (advice_{var}) {{")
    a(f"    RECOMMENDATIONS[{js_str(nid)}] = advice_{var}")
    a(f"    log(`{nid}: recommended — ${{advice_{var}.chosen}}`)")
    a("  } else {")
    a(f"    log(`{nid}: BLOCKED with no recommendation — the advisory call was "
      f"declined by the budget floor. This is a worse hand-off, not a cheaper one.`)")
    a("  }")
    a(f"  note({js_str(nid)}, 'BLOCKED', {js_str(instructions)}"
      f" + (advice_{var} ? ` | RECOMMENDED: ${{advice_{var}.chosen}} — "
      f"${{advice_{var}.rationale}}` : ''))")
    a(f"  log(`BLOCKED at {nid} ({actor}): ` + {js_str(instructions)})")
    a(f"  BLOCKED.add({js_str(nid)})")
    a("  INCOMPLETE = true")
    w = n.get("wake")
    if w:
        fields = [f"node: {js_str(nid)}", f"check: {js_str(w['check'])}",
                  f"everyMinutes: {int(w['everyMinutes'])}"]
        if w.get("deadline"):
            fields.append(f"deadline: {js_str(str(w['deadline']))}")
        a(f"  WAITS.push({{ {', '.join(fields)} }})")
    a("}")
    return "\n".join(L)


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
    # Honored decisions arrive as the record alone, at any distance and with
    # no `after` chain. Pasting the deciding node's whole result instead is
    # the context-packet smell graph-auditor already flags — and `after` is
    # single-valued, so a chain could not carry more than one hop anyway.
    for h in (n.get("honors") or []):
        mapping[f"decisions.{h}"] = f"JSON.stringify(DECISIONS[{js_str(h)}])"
    prompt = js_template(n["prompt"], mapping)
    L = []
    a = L.append
    a(f"// ===== node {nid} ({tier}{', mutates' if n.get('mutates') else ''}"
      f"{', independent' if n.get('independent') else ''}) =====")
    a(f"phase({js_str(phase)})")

    # Blocked is inherited down the `after` chain. Handing a dependent the
    # literal null of a node nobody ran would report a failure where there
    # is only a wait, and the campaign would argue with itself about why.
    has_parked = any(o.get("actor", "agent") != "agent" for o in ir["nodes"])
    guard = has_parked and n.get("after") and n.get("actor", "agent") == "agent"
    if guard:
        a(f"if (BLOCKED.has({js_str(n['after'])})) {{")
        a(f"  note({js_str(nid)}, 'BLOCKED', 'blocked on {n['after']}')")
        a(f"  log(`BLOCKED at {nid}: inherited from {n['after']}`)")
        a(f"  BLOCKED.add({js_str(nid)})")
        a("  INCOMPLETE = true")
        a(f"  RESULTS[{js_str(nid)}] = null")
        a("} else {")

    if n.get("actor", "agent") != "agent":
        a(emit_parked(n))
    elif n.get("repeat"):
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
        if n.get("irreversible"):
            key = f"k_{var}"
            a(f"const {key} = ledgerKey({js_str(nid)}, {prompt})")
            # A replayed node must not also be filed 'OK'. Reporting a
            # ceremony that did not happen the same way as one that did is
            # the whole failure this is here to prevent.
            a(f"const replayed_{var} = {key} in LEDGER")
            a(f"let {raw}")
            a(f"if (replayed_{var}) {{")
            a(f"  log(`REPLAYED-FROM-LEDGER at {nid}: already recorded against this "
              f"campaign — the effect is not performed again`)")
            a(f"  note({js_str(nid)}, 'REPLAYED', 'once-only ledger hit; effect not repeated')")
            a(f"  {raw} = LEDGER[{key}].result")
            a("} else {")
            a(f"  if (!confirmed({js_str(nid)})) {{")
            a(f"    note({js_str(nid)}, 'REFUSED', 'irreversible node not named in confirm')")
            a(f"    log(`REFUSED at {nid}: irreversible, and confirm does not name it. "
              f"Re-launch with confirm listing {nid} once a human has authorized "
              f"this specific effect.`)")
            a("    return summary('CONFIRM-REQUIRED')")
            a("  }")
            a(f"  if (!affordable({js_str('node ' + nid)})) {{")
            a(f"    note({js_str(nid)}, 'SKIPPED', 'budget floor reached')")
            if on_red == "halt":
                a("    return summary('BUDGET-EXHAUSTED')")
            a("  }")
            a(f"  {raw} = affordable({js_str('node ' + nid)}) ? await spawn({prompt}, {opts(n, ir, phase, label)}) : null")
            # Recorded the moment it returns, so the row exists even if a later
            # node halts the campaign.
            a(f"  if ({raw}) LEDGER_WRITES.push({{ key: {key}, node: {js_str(nid)}, "
              f"campaign, result: {raw} }})")
            a("}")
        else:
            a(f"if (!affordable({js_str('node ' + nid)})) {{")
            a(f"  note({js_str(nid)}, 'SKIPPED', 'budget floor reached')")
            if on_red == "halt":
                a("  return summary('BUDGET-EXHAUSTED')")
            a("}")
            a(f"const {raw} = affordable({js_str('node ' + nid)}) ? await spawn({prompt}, {opts(n, ir, phase, label)}) : null")
        # A node restored from the ledger already carries its provenance.
        if n.get("irreversible"):
            a(f"if (!replayed_{var}) {{")
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
        if n.get("irreversible"):
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
    if n.get("decides"):
        a(f"DECISIONS[{js_str(n['decides'])}] = {var}")
        a(f"if ({var} && {var}.overturned_prior) log("
          f"`DECISION {n['decides']}: overturned the prior — ${{{var}.chosen}}`)")
    if guard:
        a("}")
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
        with open(args.graph, encoding="utf-8") as fh:
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
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(js)
        print(f"graph-compile: wrote {args.out} — {len(ir['nodes'])} node(s), "
              f"{planned} planned agent call(s)")
    else:
        sys.stdout.write(js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
