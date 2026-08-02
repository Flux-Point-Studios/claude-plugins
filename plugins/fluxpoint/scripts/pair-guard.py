#!/usr/bin/env python3
"""Relation gate: check artifact PAIRS, not artifact health.

The Stop gate has two evidence channels — a harness exit code and a regex
over added lines — and both measure one artifact in isolation. That misses
an entire defect class, because the expensive failures are *relationships*:

    An on-chain predicate is tightened. Its off-chain transaction builder
    is not. Every test passes, on both sides, because each artifact is
    individually correct — and the change rejects 100% of honest
    transactions.

No count of passing tests can see that. The suite is green precisely
because nothing in it evaluates the pair together.

So the pair is declared, in a repo-owned `.fluxpoint-pairs.json`, and
checked two ways:

  co-change  A commit that moves one side of a declared pair and not the
             other is reported. Cheap, catches the common case, and says
             nothing about whether the two sides *agree* — only that
             somebody changed one and forgot the other.
  parity     A command whose job is to build the off-chain artifact and
             evaluate the on-chain one against it, so the relation itself
             becomes an exit code. This is the real check; co-change is
             the smoke alarm.

Only the consuming repo can write the parity vectors — they are specific
to the chain, the contracts, and the builder. This supplies the slot.

  pair-guard.py --check              co-change over the diff, then parity
  pair-guard.py --check --against origin/main    diff a branch, for CI
  pair-guard.py --list               what is declared, and what is not

Dormant by design: no manifest means no declared relations, exit 0.
"""
import argparse
import json
import os
import re
import subprocess
import sys

MANIFEST = ".fluxpoint-pairs.json"
PAIR_FIELDS = {"id", "source", "mirror", "parity", "symmetric", "why"}


def glob_to_re(pat):
    """Translate a path glob to a regex, with `**` spanning directories.

    fnmatch is not usable here: it treats `*` as matching `/` too, so
    `contracts/*.ak` would match a file three directories down and a pair
    would silently cover more than it claims.
    """
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def load_manifest(root):
    """Parse and validate the manifest. A malformed pair is a hard error.

    Accepting a half-declared pair would mean a relation nobody checks
    while the manifest claims coverage — the same silent-inertness failure
    the IR's closed field registry exists to prevent.
    """
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as e:
            raise SystemExit(f"pair-guard: {MANIFEST} is not valid JSON: {e}")
    if not isinstance(data, list):
        raise SystemExit(f"pair-guard: {MANIFEST} must be a list of pairs")
    for i, p in enumerate(data):
        where = f"{MANIFEST}[{i}]"
        if not isinstance(p, dict):
            raise SystemExit(f"pair-guard: {where} must be an object")
        unknown = sorted(set(p) - PAIR_FIELDS)
        if unknown:
            raise SystemExit(
                f"pair-guard: {where}: unknown field(s) {unknown} — known: "
                f"{', '.join(sorted(PAIR_FIELDS))}")
        if not p.get("id"):
            raise SystemExit(f"pair-guard: {where}: id required")
        if not p.get("source") or not as_list(p.get("mirror")):
            raise SystemExit(
                f"pair-guard: {where} ('{p.get('id')}'): both source and mirror "
                f"are required — a pair with one side is not a relation")
    return data


def changed_files(root, against):
    """Paths this change touches, tracked edits plus new untracked files.

    Mirrors the Stop gate's rule rather than inventing a second one: the
    marker cannot see files written through the Bash tool, so dirtiness is
    re-derived from git.
    """
    def git(*a):
        r = subprocess.run(["git", "-C", root, *a], capture_output=True, text=True)
        return [x for x in r.stdout.splitlines() if x] if r.returncode == 0 else []

    if against == "HEAD":
        files = git("diff", "HEAD", "--name-only", "--diff-filter=ACMR")
        files += git("ls-files", "--others", "--exclude-standard")
    else:
        files = git("diff", f"{against}...HEAD", "--name-only", "--diff-filter=ACMR")
        files += git("diff", "HEAD", "--name-only", "--diff-filter=ACMR")
    return sorted(set(files))


def matches(patterns, files):
    hit = []
    for pat in patterns:
        rx = glob_to_re(pat)
        hit += [f for f in files if rx.match(f)]
    return sorted(set(hit))


def check(root, against, run_parity, report_only):
    pairs = load_manifest(root)
    if pairs is None:
        print(f"pair-guard: no {MANIFEST} — no declared relations to check")
        return 0
    if not pairs:
        print(f"pair-guard: {MANIFEST} declares no pairs")
        return 0

    files = changed_files(root, against)
    failures, checked = [], 0

    for p in pairs:
        src = matches(as_list(p["source"]), files)
        mir = matches(as_list(p["mirror"]), files)
        if src and not mir:
            failures.append(
                f"{p['id']}: {', '.join(src[:3])} changed, but nothing matching "
                f"{as_list(p['mirror'])} did"
                + (f" — {p['why']}" if p.get("why") else ""))
        elif mir and not src and p.get("symmetric"):
            failures.append(
                f"{p['id']}: {', '.join(mir[:3])} changed, but nothing matching "
                f"'{p['source']}' did"
                + (f" — {p['why']}" if p.get("why") else ""))
        if src or mir:
            checked += 1

    if files:
        print(f"pair-guard: {len(pairs)} declared pair(s), {checked} touched by "
              f"this change")
    else:
        print(f"pair-guard: {len(pairs)} declared pair(s), no changes to compare")

    # Parity is the real check and runs regardless of what moved: two sides
    # can disagree without either being edited today, and --full is meant to
    # be everything the Definition of Done requires.
    if run_parity:
        for p in pairs:
            cmd = p.get("parity")
            if not cmd:
                print(f"  {p['id']}: co-change only — no parity command declared")
                continue
            r = subprocess.run(cmd, shell=True, cwd=root,
                               capture_output=True, text=True)
            if r.returncode == 0:
                print(f"  {p['id']}: parity ok")
            else:
                tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
                failures.append(
                    f"{p['id']}: parity command failed (exit {r.returncode}): {cmd}"
                    + ("\n      " + "\n      ".join(tail) if tail else ""))

    if failures and not report_only:
        print("\npair-guard: RED — a declared relation is broken or unverified\n",
              file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        print("\n  Both artifacts can pass their own tests while the pair is "
              "wrong.\n", file=sys.stderr)
        return 1
    if not failures:
        print("pair-guard: green")
    return 0


def listing(root):
    pairs = load_manifest(root)
    if pairs is None:
        print(f"pair-guard: no {MANIFEST}")
        return 0
    for p in pairs:
        kind = "co-change + parity" if p.get("parity") else "co-change only"
        print(f"  {p['id']:<24} {kind}")
        print(f"      source: {p['source']}")
        print(f"      mirror: {', '.join(as_list(p['mirror']))}")
        if not p.get("parity"):
            # Named, because a co-change rule proves only that somebody
            # remembered to touch both files, never that they agree.
            print("      no parity command — nothing evaluates the two together")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--against", default="HEAD",
                    help="ref to diff against; HEAD means the working tree")
    ap.add_argument("--no-parity", action="store_true",
                    help="co-change only, for a fast pre-commit pass")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--report", action="store_true")
    g.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        return listing(a.root)
    return check(a.root, a.against, not a.no_parity, a.report)


if __name__ == "__main__":
    sys.exit(main())
