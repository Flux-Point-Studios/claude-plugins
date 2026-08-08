#!/usr/bin/env python3
"""Statement ratchet: what is being proved may not quietly get weaker.

`proof-guard.py` polices proof *bodies* — the `todo`, `assume`, `sorry` and
`{:axiom}` that discharge an obligation without proving it. It has nothing
to say about the obligation itself. An agent blocked from adding an
`assume` has an easier move available: weaken the theorem. Drop a conjunct
from an `ensures`. Widen a `requires` until the hard case is out of scope.
Delete the property test that covered the attack. Rename it so nothing
points at it any more.

Every one of those keeps the hatch counts flat, keeps the checker exiting
0, and keeps the suite green. The `proof-auditor` agent's own checklist
calls this the most common way verified code regresses, and until now it
was caught only if a model happened to notice.

So the statements are recorded, hashed, and compared:

  spec-guard.py --scan       list every obligation this repo declares
  spec-guard.py --baseline   record them into .fluxpoint-proof-baseline.json
  spec-guard.py --check      fail when a recorded obligation changed or vanished

Adding obligations is always free — the ratchet only turns one way, exactly
like proof-guard's counts. A *changed* or *removed* obligation needs one of
two things: a Decisions row in the work file naming the obligation id, or a
re-recorded baseline. Both leave the weakening in a committed diff with
someone's name on it, which is the whole point; neither pretends to make
weakening impossible. A ratchet makes it loud, not unreachable.

Coverage, stated rather than assumed: Aiken test and property signatures,
and Dafny requires/ensures/invariant clauses per declaration. Lean, Coq,
Isabelle and TLA+ files are NOT parsed yet — `--scan` and `--check` say so
by name when they are present, because a guard that silently covers nothing
is worse than one that is absent.

What a statement hash cannot see: a property proved about an unreachable
state, a generator that cannot produce the interesting case, a test whose
body was gutted while its signature held. The first two are the
`proof-auditor` agent's job. The third is `mutation-guard`'s.
"""
import argparse
import json
import os
import re
import subprocess
import sys

BASELINE = ".fluxpoint-proof-baseline.json"

# Tools whose obligations this script can actually read. Anything else that
# looks like proof work is reported as uncovered rather than passed over.
COVERED = {".ak", ".dfy"}
UNCOVERED = {
    ".lean": "Lean", ".v": "Coq/Rocq", ".thy": "Isabelle", ".tla": "TLA+",
}

# `test name(params) fail {` — the signature is the obligation. The fuzzer
# types in the params are part of it: narrowing a generator narrows what was
# actually checked, and `fail` inverts the whole claim.
#
# Only the head is matched here; the parameter list is read by matching
# parens, because a fuzzer carries its own — `n: Int via bounded_int(1, 99)`
# — and a `[^)]*` class stops at the first one, which makes exactly the
# property tests this ratchet exists to protect invisible to it.
AIKEN_TEST_HEAD = re.compile(
    r"(?:^|\n)[ \t]*test[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
# A Dafny declaration that can carry a specification.
DAFNY_DECL = re.compile(
    r"^\s*(?:ghost\s+)?(?:method|function|lemma|predicate|twostate\s+\w+)"
    r"\s+([A-Za-z_][A-Za-z0-9_']*)")
# The clauses that ARE the specification. `invariant` is included because a
# loop invariant that loses a conjunct weakens the proof exactly as an
# `ensures` does.
DAFNY_CLAUSE = re.compile(r"^\s*(requires|ensures|invariant)\b(.*)$")
COMMENT = {".ak": "//", ".dfy": "//"}


def tracked_files(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"],
                             capture_output=True, text=True, check=True).stdout
    except Exception:  # noqa: BLE001
        return []
    return [f for f in out.splitlines() if f]


def strip_comment(line, suffix):
    marker = COMMENT.get(suffix)
    if not marker:
        return line
    i = line.find(marker)
    return line[:i] if i >= 0 else line


def norm(s):
    """Whitespace-insensitive form. Reformatting is not weakening."""
    return " ".join(str(s).split())


def sha(text):
    import hashlib
    return hashlib.sha256(norm(text).encode("utf-8", "replace")).hexdigest()[:16]


def _matching_paren(text, i):
    """Index of the `)` closing the `(` at i, or -1."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def scan_aiken(rel, lines):
    """One obligation per test: its name, its generators, its polarity."""
    text = "\n".join(strip_comment(l.rstrip("\n"), ".ak") for l in lines)
    out = []
    for m in AIKEN_TEST_HEAD.finditer(text):
        name = m.group(1)
        close = _matching_paren(text, m.end() - 1)
        if close < 0:
            continue
        rest = text[close + 1:]
        brace = rest.find("{")
        if brace < 0:
            continue
        params = norm(text[m.end():close])
        fail = bool(re.match(r"\s*fail\b", rest[:brace]))
        statement = f"({params}){' fail' if fail else ''}"
        out.append({"id": f"aiken:{rel}:{name}", "tool": "aiken", "file": rel,
                    "name": name, "statement": statement})
    return out


def scan_dafny(rel, lines):
    """One obligation per declaration that carries a specification.

    Clauses are sorted, so reordering them is not a change; a declaration
    that had clauses and now has none has lost its obligation entirely,
    which is the case this exists to catch.
    """
    out, current, clauses = [], None, []

    def flush():
        if current and clauses:
            out.append({
                "id": f"dafny:{rel}:{current}", "tool": "dafny", "file": rel,
                "name": current, "statement": " ; ".join(sorted(clauses)),
            })

    for raw in lines:
        line = strip_comment(raw, ".dfy")
        d = DAFNY_DECL.match(line)
        if d:
            flush()
            current, clauses = d.group(1), []
            continue
        c = DAFNY_CLAUSE.match(line)
        if c and current:
            clauses.append(norm(f"{c.group(1)} {c.group(2)}"))
    flush()
    return out


def scan(root):
    """Return (obligations_by_id, uncovered_tools_present)."""
    obligations, uncovered = {}, set()
    for rel in tracked_files(root):
        suffix = os.path.splitext(rel)[1]
        if suffix in UNCOVERED:
            uncovered.add(UNCOVERED[suffix])
            continue
        if suffix not in COVERED:
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        found = scan_aiken(rel, lines) if suffix == ".ak" else scan_dafny(rel, lines)
        for o in found:
            o["statementSha"] = sha(o["statement"])
            obligations[o["id"]] = o
    return obligations, uncovered


def load_baseline(root):
    p = os.path.join(root, BASELINE)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"spec-guard: {p} is not readable JSON: {e}")


def write_baseline(root, obligations):
    """Record the spec section, preserving everything else in the file.

    proof-guard owns `counts` in this same file and rewrites it on its own
    `--baseline`; each script must leave the other's section alone or arming
    one ratchet would silently disarm the other.
    """
    p = os.path.join(root, BASELINE)
    doc = load_baseline(root) or {"version": 1}
    doc["spec"] = {
        "note": ("Obligation statements, hashed. spec-guard.py --check fails "
                 "when one changes or disappears without a Decisions row "
                 "naming its id. Adding obligations is always allowed."),
        "obligations": {
            oid: {"tool": o["tool"], "file": o["file"], "name": o["name"],
                  "statementSha": o["statementSha"]}
            for oid, o in sorted(obligations.items())
        },
    }
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return p


def work_text(root):
    """The Decisions section of the repo's work file, if there is one."""
    for name in ("WORK.md", "LOOP.md"):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"^##\s+Decisions\s*$(.*?)(?=^##\s|\Z)", text,
                      re.S | re.M | re.I)
        return m.group(1) if m else ""
    return ""


def check(root):
    """Return (findings, notes). A finding is an unjustified weakening."""
    base = load_baseline(root)
    now, uncovered = scan(root)
    notes = []
    if uncovered:
        notes.append(
            f"NOT covered by this ratchet: {', '.join(sorted(uncovered))} "
            f"file(s) are tracked but their obligations are not parsed yet")
    if base is None or "spec" not in base:
        if now:
            notes.append(
                f"{len(now)} obligation(s) found but the spec ratchet is NOT "
                f"armed. Run: spec-guard.py --baseline")
        return [], notes
    recorded = base["spec"].get("obligations", {})
    justified = work_text(root)
    findings = []
    # A file rename moves every obligation in it. That is not a weakening, so
    # an id that vanished while an identical statement appeared under the same
    # name elsewhere is treated as the same obligation, relocated.
    moved = {}
    for oid, was in recorded.items():
        if oid in now:
            continue
        for nid, o in now.items():
            if (nid not in recorded and o["name"] == was.get("name")
                    and o["tool"] == was.get("tool")
                    and o["statementSha"] == was.get("statementSha")):
                moved[oid] = nid
                break
    for oid, was in sorted(recorded.items()):
        if oid in moved:
            notes.append(f"{oid} moved to {moved[oid]} (statement unchanged)")
            continue
        if oid in justified:
            notes.append(f"{oid} changed, and a Decisions row names it")
            continue
        if oid not in now:
            findings.append(
                (oid, f"REMOVED — {was.get('tool')} obligation '{was.get('name')}' "
                      f"is gone from {was.get('file')}"))
        elif now[oid]["statementSha"] != was.get("statementSha"):
            findings.append(
                (oid, f"CHANGED — the statement is not what was recorded\n"
                      f"        now: {now[oid]['statement'][:120]}"))
    added = [oid for oid in now if oid not in recorded and oid not in moved.values()]
    if added:
        notes.append(f"{len(added)} obligation(s) added since the baseline "
                     f"(always allowed; re-record to lock them in)")
    return findings, notes


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.scan:
        obligations, uncovered = scan(a.root)
        if not obligations and not uncovered:
            print("spec-guard: no obligations in a covered language — dormant")
            return 0
        print(f"spec-guard: {len(obligations)} obligation(s)")
        for oid, o in sorted(obligations.items()):
            print(f"  {oid}")
            print(f"      {o['statement'][:110]}")
        for tool in sorted(uncovered):
            print(f"  NOT COVERED: {tool} obligations are not parsed yet")
        return 0

    if a.baseline:
        obligations, uncovered = scan(a.root)
        p = write_baseline(a.root, obligations)
        print(f"spec-guard: recorded {len(obligations)} obligation(s) into {p}")
        for tool in sorted(uncovered):
            print(f"spec-guard: NOT COVERED — {tool} obligations are not parsed yet")
        return 0

    findings, notes = check(a.root)
    for n in notes:
        print(f"spec-guard: {n}")
    if not findings:
        print("spec-guard: green — no recorded obligation weakened")
        return 0
    print("\nspec-guard: RED — a proof obligation got weaker\n", file=sys.stderr)
    for oid, detail in findings:
        print(f"  {oid}: {detail}", file=sys.stderr)
    print(
        "\n  A statement that changed or vanished is a weaker claim than the one\n"
        "  that was recorded, and every checker still exits 0 on it. Restore it,\n"
        "  or justify the change: add a Decisions row naming the obligation id,\n"
        "  or re-record with --baseline so the weakening lands in a reviewed diff.",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
