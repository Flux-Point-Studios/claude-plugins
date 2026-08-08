#!/usr/bin/env python3
"""Execution attestation: exit codes minted by a hook, not typed by an agent.

The compiler's flagship ordering invariant — an independent node re-derives
the verdict, and its `haltWhen` guards the irreversible effect ordered after
it — is sound in structure and hollow in fidelity. The gate node *runs*
`scripts/harness.sh` and then *writes* `{"exit": 0}` into HarnessCheckV1 by
hand. The integer that stands between a campaign and an unrepeatable chain
write is a transcription, and `record-run.py` treats it as derived truth.

So the exit code gets recorded where it actually happens. A PostToolUse hook
on the Bash tool sees the command and the runtime's own `tool_response`, and
when the command is one this repo declared a gate, appends a row here. The
agent never touches the number.

  attest.py --record          append a row from a hook payload on stdin
  attest.py --list            what has been attested, for humans
  attest.py --verify          cross-check a run summary on stdin against the log

Dormant by design: a repo with no `.fluxpoint-gates.json` attests nothing
and says nothing, exactly like the DoD gate in a repo with no harness.

Two limits, stated rather than papered over. The hook only sees executions
that go through the Bash tool, so a gate run some other way produces no row
— which is why an unattested claim is reported UNATTESTED and never as a
failure; treating absence as guilt would make this a false-red generator on
the first executor that does not route through the hook. And the log records
what a command exited with, not whether the command was worth running: a
declared gate that is itself weakened is `fpl_harness_modified`'s problem
and the proof-guard ratchet's, not this file's.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

GATES = ".fluxpoint-gates.json"
ATTEST = os.path.join(".claude", "fluxpoint", "attest.jsonl")
GATE_MANIFEST_FIELDS = {"version", "gates"}
IDENT = re.compile(r"^[a-z][a-z0-9-]*$")


def path_for(root):
    return os.path.join(root, ATTEST)


def gates_path(root):
    return os.path.join(root, GATES)


def normalize(cmd):
    """Canonical form of a command line, for comparison against a manifest.

    Whitespace is collapsed and a leading interpreter or `./` is dropped, so
    `bash scripts/harness.sh  --full` and `./scripts/harness.sh --full` are
    the same declared gate.

    Nothing else is stripped, and that is the load-bearing part. A pipeline,
    a redirect, or a trailing `|| true` changes the exit code the runtime
    reports, so it must not be able to borrow a gate's name: it simply does
    not match, and an unmatched command is attested as nothing at all.
    """
    s = " ".join(str(cmd or "").split())
    for prefix in ("bash ", "sh ", "zsh "):
        if s.startswith(prefix):
            s = s[len(prefix):].lstrip()
            break
    if s.startswith("./"):
        s = s[2:]
    return s


def sha(text):
    return hashlib.sha256(str(text).encode("utf-8", "replace")).hexdigest()


def load_gates(root):
    """Return (gates, findings). Missing manifest is dormant, not an error.

    A malformed manifest is a finding rather than an empty gate set: a
    manifest that silently parses to nothing disarms attestation while
    looking configured, which is the failure mode this whole plugin exists
    to refuse.
    """
    p = gates_path(root)
    if not os.path.exists(p):
        return {}, []
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        return {}, [f"{GATES} is not readable JSON ({e}) — attestation is DISARMED"]
    if not isinstance(doc, dict):
        return {}, [f"{GATES} must be an object"]
    f = [f"{GATES}: unknown field '{k}' — not part of the manifest"
         for k in sorted(set(doc) - GATE_MANIFEST_FIELDS)]
    if doc.get("version") != 1:
        f.append(f"{GATES}: version must be 1")
    gates = doc.get("gates")
    if not isinstance(gates, dict) or not gates:
        f.append(f"{GATES}: 'gates' must be a non-empty object of name -> command")
        return {}, f
    out = {}
    for name, cmd in gates.items():
        if not IDENT.match(str(name)):
            f.append(f"{GATES}: gate name '{name}' must be lowercase kebab-case")
            continue
        if not isinstance(cmd, str) or not cmd.strip():
            f.append(f"{GATES}.{name}: command must be a non-empty string")
            continue
        out[name] = normalize(cmd)
    return ({} if f else out), f


def gate_for(gates, command):
    """The declared gate this exact command is, or None."""
    n = normalize(command)
    for name, declared in gates.items():
        if declared == n:
            return name
    return None


def read(root):
    """Every attested execution, oldest first. A malformed line is fatal.

    Same discipline as the once-only ledger, for the same reason: skipping
    an unparseable row would silently shrink the set of executions known to
    have happened, and this file's whole job is to be the thing that cannot
    quietly lose one.
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
                raise SystemExit(f"attest: {p}:{i} is not valid JSON: {e}")
    return out


def head_sha(root):
    """Best-effort HEAD, so a row says which tree the gate judged."""
    try:
        import subprocess
        r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - provenance detail, never worth failing over
        return ""


def record(root, payload):
    """Append a row for a hook payload. Returns (row, findings).

    (None, []) means the command was not a declared gate — the common case,
    and deliberately silent.
    """
    gates, findings = load_gates(root)
    if findings or not gates:
        return None, findings
    tool = payload.get("tool_name")
    if tool and tool != "Bash":
        return None, []
    command = (payload.get("tool_input") or {}).get("command")
    gate = gate_for(gates, command)
    if not gate:
        return None, []
    resp = payload.get("tool_response")
    if not isinstance(resp, dict) or not isinstance(resp.get("exit_code"), int):
        # The gate ran and its verdict was unreadable. Silence here would be
        # indistinguishable from the gate never running, so say so.
        return None, [
            f"gate '{gate}' ran but the hook payload carried no integer "
            f"tool_response.exit_code — nothing was attested for this run"]
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    norm = normalize(command)
    log = f"{resp.get('stdout') or ''}\n---\n{resp.get('stderr') or ''}"
    session = str(payload.get("session_id") or "")
    row = {
        "attestId": "att_" + sha(f"{when}|{norm}|{resp['exit_code']}|{session}"
                                 f"|{payload.get('tool_use_id') or ''}")[:12],
        "gate": gate,
        "command": norm,
        "commandSha": sha(norm),
        "exit": resp["exit_code"],
        "logSha256": sha(log),
        "headSha": head_sha(root),
        "when": when,
        "sessionId": session,
        # Subagent Bash calls fire this hook too, and a gate run inside a
        # graph node is exactly the execution record/run needs to bind.
        "agent": str(payload.get("agent_type") or payload.get("agent_id") or ""),
    }
    p = path_for(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    return row, []


def _each(value):
    if isinstance(value, list):
        for v in value:
            if isinstance(v, dict):
                yield v
    elif isinstance(value, dict):
        yield value


def verify_claims(root, summary):
    """Cross-check a run summary's claimed gate exits against the log.

    Returns a list of checks, one per node result that claims to have run a
    declared gate. Nodes that ran something else are not checked and not
    reported — this speaks only about commands the repo itself declared.
    """
    gates, findings = load_gates(root)
    if findings or not gates:
        return [], findings
    rows = read(root)
    results = (summary or {}).get("results") or {}
    contracts = (summary or {}).get("contracts") or {}
    checks = []
    for node, value in results.items():
        for r in _each(value):
            if "exit" not in r or "command" not in r:
                continue
            c = contracts.get(node)
            if c not in (None, "HarnessCheckV1", "ExecutionV1"):
                continue
            gate = gate_for(gates, r.get("command"))
            if not gate:
                continue
            claimed = r.get("exit")
            mine = [a for a in rows if a.get("commandSha") == sha(normalize(r["command"]))]
            hit = next((a for a in mine if a.get("exit") == claimed), None)
            if hit:
                checks.append({
                    "node": node, "gate": gate, "claimedExit": claimed,
                    "status": "ATTESTED", "attestId": hit.get("attestId"),
                    "detail": f"exit {claimed} attested {hit.get('attestId')}"})
            elif mine:
                newest = mine[-1]
                checks.append({
                    "node": node, "gate": gate, "claimedExit": claimed,
                    "status": "MISMATCH", "attestId": newest.get("attestId"),
                    "detail": (f"node claims exit {claimed} for gate '{gate}', but no "
                               f"attested run of it exited {claimed}; the most recent "
                               f"exited {newest.get('exit')} "
                               f"({newest.get('attestId')} at {newest.get('when')})")})
            else:
                checks.append({
                    "node": node, "gate": gate, "claimedExit": claimed,
                    "status": "UNATTESTED", "attestId": None,
                    "detail": (f"gate '{gate}' has no attested execution at all — the "
                               f"exit code rests on the node's own report")})
    return checks, []


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--verify", action="store_true")
    a = ap.parse_args()

    if a.list:
        gates, findings = load_gates(a.root)
        for f in findings:
            print(f"attest: {f}", file=sys.stderr)
        if findings:
            return 1
        if not gates:
            print(f"attest: no {GATES} in this repo — attestation is dormant")
            return 0
        rows = read(a.root)
        print(f"attest: {len(gates)} declared gate(s), {len(rows)} attested execution(s)")
        for name, cmd in sorted(gates.items()):
            mine = [r for r in rows if r.get("gate") == name]
            last = f"last {mine[-1]['when']} exit {mine[-1]['exit']}" if mine else "never run"
            print(f"  {name:<16} {cmd}")
            print(f"  {'':<16} {len(mine)} run(s), {last}")
        return 0

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(f"attest: input is not JSON: {e}", file=sys.stderr)
        return 1

    if a.record:
        row, findings = record(a.root, payload)
        for f in findings:
            print(f"attest: {f}", file=sys.stderr)
        if findings:
            return 1
        if row:
            print(f"attest: {row['gate']} exit {row['exit']} -> {row['attestId']}")
        return 0

    checks, findings = verify_claims(a.root, payload)
    for f in findings:
        print(f"attest: {f}", file=sys.stderr)
    if findings:
        return 1
    if not checks:
        print("attest: no declared gate claims in this summary")
        return 0
    bad = 0
    for c in checks:
        print(f"  [{c['status']}] {c['node']}: {c['detail']}")
        bad += c["status"] == "MISMATCH"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
