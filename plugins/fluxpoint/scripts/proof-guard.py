#!/usr/bin/env python3
"""Proof-strength ratchet: escape hatches may go down, never up.

A prover's exit code is a much better signal than "the code looks right",
which is why this harness is well suited to verified work. But exit 0 does
not mean what it looks like. Every proof assistant ships a way to make a
goal go away without proving it — `todo` in Aiken, `assume` and `{:axiom}`
in Dafny, `sorry` in Lean, `Admitted` in Coq, `#[verifier::external_body]`
in Verus — and a checker exits 0 just as happily on an assumed lemma as on
a discharged one. An agent asked to "make the proof pass" will find those.

So the count of escape hatches is recorded in a committed baseline and this
script fails when any category rises. Weakening a proof stops being a
judgment call and becomes a diff someone has to justify. Going the other
way is always allowed: proving something you previously assumed lowers the
count, and `--baseline` re-records it.

  proof-guard.py --scan       report current counts and where they are
  proof-guard.py --baseline   record counts to .fluxpoint-proof-baseline.json
  proof-guard.py --check      fail if any category rose above the baseline

Dormant by design: a repo with no proof-language files exits 0 in silence,
exactly like the DoD gate in a repo with no harness.

What this cannot see, verified by trying it: counting escape hatches says
nothing about whether a proof still means anything. Gutting an Aiken
negative test to `True` removes all of its force and leaves every count
unchanged, so this script reports green. The same is true of a theorem
whose statement lost a conjunct, a property proved about an unreachable
state, or a solver `unknown` read as success. Those are the
`proof-auditor` agent's job — run `/fluxpoint:proof-audit`, which does
both passes. A ratchet is a floor, not a ceiling.
"""
import argparse
import json
import os
import re
import subprocess
import sys

BASELINE = ".fluxpoint-proof-baseline.json"

# (category, file suffixes, regex). Categories are namespaced by tool so a
# repo that adds a second prover ratchets each independently.
#
# These are the documented ways to discharge an obligation without proving
# it. Some are legitimate in context — an `expect` in an Aiken validator, a
# negative `fail` test — which is exactly why this ratchets rather than
# forbids: the count may hold or fall, and a rise needs a human.
PATTERNS = [
    # --- Aiken ---------------------------------------------------------
    ("aiken.todo",          (".ak",),   r"\btodo\b"),
    ("aiken.expect",        (".ak",),   r"\bexpect\b"),
    ("aiken.trace",         (".ak",),   r"\btrace\b"),
    # --- Dafny ---------------------------------------------------------
    ("dafny.assume",        (".dfy",),  r"\bassume\b"),
    ("dafny.axiom",         (".dfy",),  r"\{:axiom\}"),
    ("dafny.verify_false",  (".dfy",),  r"\{:verify\s+false\}"),
    ("dafny.extern",        (".dfy",),  r"\{:extern\b"),
    # --- Lean ----------------------------------------------------------
    ("lean.sorry",          (".lean",), r"\bsorry\b"),
    ("lean.axiom",          (".lean",), r"^\s*axiom\b"),
    ("lean.native_decide",  (".lean",), r"\bnative_decide\b"),
    # --- Coq / Rocq ----------------------------------------------------
    ("coq.admitted",        (".v",),    r"\b(Admitted|admit)\b"),
    ("coq.axiom",           (".v",),    r"^\s*(Axiom|Parameter|Hypothesis)\b"),
    # --- Rust verification (Verus, Prusti, Kani) ------------------------
    ("rust.external_body",  (".rs",),   r"#\[verifier::external(_body)?\]"),
    ("rust.trusted",        (".rs",),   r"#\[trusted\]"),
    ("rust.assume",         (".rs",),   r"\b(kani::assume|prusti_assume|assume)\s*\("),
    # --- Isabelle -------------------------------------------------------
    ("isabelle.sorry",      (".thy",),  r"\b(sorry|oops)\b"),
    # --- TLA+ / Apalache -------------------------------------------------
    ("tla.assume",          (".tla",),  r"^\s*ASSUME\b"),
    # --- Verification disabled from the command line ---------------------
    # A flag in a script or CI config that turns the checker off is the
    # bluntest escape hatch of all, and the easiest to miss in review.
    ("flags.verification_off",
     (".sh", ".yml", ".yaml", ".toml", ".json", ".just", ".mk", "Makefile"),
     r"--(no-verify|skip-tests|skip-verification|no-check|dont-verify)\b"),
]

# Line-comment prefixes, so prose like "we assume the oracle is honest" in a
# comment does not register as an assumption in the proof.
COMMENT = {
    ".ak": "//", ".rs": "//", ".dfy": "//", ".lean": "--", ".tla": r"\\\*",
    ".thy": None, ".v": None, ".sh": "#", ".yml": "#", ".yaml": "#",
    ".toml": "#", ".just": "#", ".mk": "#", "Makefile": "#", ".json": None,
}


def tracked_files(root):
    """Only git-tracked files: build output and vendored deps are not ours."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
    except Exception:
        return []
    return [f for f in out.splitlines() if f]


def strip_comment(line, suffix):
    marker = COMMENT.get(suffix)
    if not marker:
        return line
    m = re.search(marker, line)
    return line[: m.start()] if m else line


def scan(root):
    """Return (counts, hits) where hits is category -> ['path:line: text']."""
    counts, hits = {c: 0 for c, _, _ in PATTERNS}, {c: [] for c, _, _ in PATTERNS}
    compiled = [(c, sfx, re.compile(rx)) for c, sfx, rx in PATTERNS]
    for rel in tracked_files(root):
        base = os.path.basename(rel)
        suffix = base if base in ("Makefile",) else os.path.splitext(rel)[1]
        if not suffix:
            continue
        applicable = [(c, rx) for c, sfx, rx in compiled if suffix in sfx]
        if not applicable:
            continue
        path = os.path.join(root, rel)
        try:
            with open(path, errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for n, raw in enumerate(lines, 1):
            line = strip_comment(raw, suffix)
            for cat, rx in applicable:
                if rx.search(line):
                    counts[cat] += 1
                    if len(hits[cat]) < 20:
                        hits[cat].append(f"{rel}:{n}: {raw.strip()[:100]}")
    return counts, hits


def applicable(counts, root):
    """True when this repo has anything worth ratcheting."""
    suffixes = {os.path.splitext(f)[1] for f in tracked_files(root)}
    proof_suffixes = {s for _, sfx, _ in PATTERNS for s in sfx if s.startswith(".")}
    return bool(suffixes & (proof_suffixes - {".sh", ".yml", ".yaml", ".toml", ".json"}))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()
    root = args.root
    bpath = os.path.join(root, BASELINE)

    counts, hits = scan(root)
    nonzero = {k: v for k, v in counts.items() if v}

    if args.scan:
        if not applicable(counts, root):
            print("proof-guard: no proof-language files tracked — dormant")
            return 0
        print("proof-guard: escape hatches by category")
        for cat in sorted(counts):
            if counts[cat]:
                print(f"  {cat:<26} {counts[cat]}")
                for h in hits[cat][:5]:
                    print(f"      {h}")
        if not nonzero:
            print("  none found")
        return 0

    if args.baseline:
        with open(bpath, "w") as fh:
            json.dump(
                {
                    "version": 1,
                    "note": (
                        "Escape-hatch counts. proof-guard.py --check fails if any "
                        "category rises. Lowering one (proving what was assumed) is "
                        "always allowed; re-record with --baseline and explain the "
                        "rise in review if a count must go up."
                    ),
                    "counts": counts,
                },
                fh,
                indent=2,
                sort_keys=True,
            )
            fh.write("\n")
        print(f"proof-guard: wrote {BASELINE} ({sum(counts.values())} hatch(es) recorded)")
        return 0

    # --check
    if not os.path.exists(bpath):
        if not applicable(counts, root):
            return 0  # dormant, like the gate in a repo with no harness
        print(
            f"proof-guard: proof files are tracked but {BASELINE} is absent, so the "
            f"ratchet is NOT armed. Run: proof-guard.py --baseline",
            file=sys.stderr,
        )
        return 0  # bootstrapping must not block; arming is a deliberate step

    with open(bpath) as fh:
        base = json.load(fh).get("counts", {})

    risen = [
        (cat, base.get(cat, 0), counts[cat])
        for cat in sorted(counts)
        if counts[cat] > base.get(cat, 0)
    ]
    if risen:
        print("proof-guard: RED — proof strength decreased\n", file=sys.stderr)
        for cat, was, now in risen:
            print(f"  {cat}: {was} -> {now}", file=sys.stderr)
            for h in hits[cat][:8]:
                print(f"      {h}", file=sys.stderr)
        print(
            "\n  An escape hatch discharges a goal without proving it, so the checker "
            "still exits 0.\n  Prove it, or justify the rise in review and re-record "
            "with --baseline.",
            file=sys.stderr,
        )
        return 1

    fell = [(c, base.get(c, 0), counts[c]) for c in sorted(counts) if counts[c] < base.get(c, 0)]
    if fell:
        print("proof-guard: green — and stronger than the baseline:")
        for cat, was, now in fell:
            print(f"  {cat}: {was} -> {now} (re-record with --baseline to lock it in)")
    else:
        print(f"proof-guard: green ({sum(counts.values())} hatch(es), none risen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
